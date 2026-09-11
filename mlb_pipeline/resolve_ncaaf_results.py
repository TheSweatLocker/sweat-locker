"""NCAAF post-game resolver — refresh scores from CFBD, update outcomes,
grade external picks.

Runs Sunday morning during CFB season (after Saturday slate) + Monday
morning (after MNF... wait, MNF is NFL). CFB has Sat main + Thu/Fri
weeknights + Sun morning window is post-Saturday game day.

Flow:
  1. Pull CFBD /games for the current + prior weeks to catch late score
     updates (CFBD sometimes lags 1-2 hrs post-final).
  2. PATCH ncaaf_game_results with fresh home_score/away_score →
     computes total_points, home_win, spread_result, total_result.
  3. Kick off resolve_externals.py --sport NCAAF to grade any external
     picks that reference NCAAF games.

Idempotent — safe to re-run. Only writes when scores change.

USAGE:
    python resolve_ncaaf_results.py               # current season
    python resolve_ncaaf_results.py --season 2024 # specific season
    python resolve_ncaaf_results.py --dry-run
"""
import argparse
import os
import sys
import subprocess
from datetime import datetime, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
CFBD_KEY = os.environ.get('CFBD_API_KEY')

H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

CFBD_BASE = 'https://api.collegefootballdata.com'


def _f(v):
    try: return float(v) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(float(v)) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def cfbd_get(path: str, params: dict) -> list:
    if not CFBD_KEY: return []
    r = requests.get(
        f'{CFBD_BASE}{path}',
        headers={'Authorization': f'Bearer {CFBD_KEY}'},
        params=params, timeout=30,
    )
    if r.status_code != 200:
        print(f'  ⚠ CFBD {path} {r.status_code}')
        return []
    return r.json()


def fetch_games_for_scoring(season: int) -> list:
    """Pull ALL games for the season (regular + postseason) with scores."""
    games = []
    for st in ('regular', 'postseason'):
        games.extend(cfbd_get('/games', {'year': season, 'seasonType': st, 'division': 'fbs'}))
    # Only care about games with scores (completed)
    return [g for g in games
            if _i(g.get('home_points') or g.get('homePoints')) is not None]


def compute_outcome_patch(cfbd_game: dict, existing: dict) -> Optional[dict]:
    """Build a PATCH payload with fresh scores + computed outcomes.
    Returns None if nothing changed (avoid unnecessary writes)."""
    home_score = _i(cfbd_game.get('home_points') or cfbd_game.get('homePoints'))
    away_score = _i(cfbd_game.get('away_points') or cfbd_game.get('awayPoints'))
    if home_score is None or away_score is None:
        return None
    existing_home = _i(existing.get('home_score'))
    existing_away = _i(existing.get('away_score'))
    # 2026-09-11 FIX: previously returned None when scores unchanged, but that
    # skipped filling in spread_result/total_result for rows where scores had
    # been imported by a different loader (e.g. Miami vs FAMU 77-7 was seeded
    # with scores but spread_result/total_result stayed null forever). Now
    # only skip if scores AND both derived fields are already present.
    scores_stable = (existing_home == home_score and existing_away == away_score)
    already_derived = (existing.get('spread_result') is not None
                       and existing.get('total_result') is not None)
    if scores_stable and already_derived:
        return None  # nothing to do

    payload = {
        'home_score': home_score,
        'away_score': away_score,
        'total_points': home_score + away_score,
        'home_win': home_score > away_score,
        'overtime': bool(cfbd_game.get('overtime')),
    }

    # Compute spread_result if we have close_spread
    cs = _f(existing.get('close_spread'))
    if cs is not None:
        # CFBD convention: positive = home dog. Home covers when
        # margin > -close_spread.
        margin = home_score - away_score
        if margin > -cs:   payload['spread_result'] = 'home_covered'
        elif margin < -cs: payload['spread_result'] = 'away_covered'
        else:              payload['spread_result'] = 'push'

    # Compute total_result if we have close_total
    ct = _f(existing.get('close_total'))
    if ct is not None:
        tot = home_score + away_score
        if tot > ct:   payload['total_result'] = 'over'
        elif tot < ct: payload['total_result'] = 'under'
        else:          payload['total_result'] = 'push'

    return payload


def _fold_name(name: str) -> str:
    """Normalize team name for cross-source matching.

    - Lowercase
    - Strip common mascot suffixes (Hornets, Bison, Rams, Wildcats, ...)
      that leak into game_ids from earlier ncaaf_odds_pull runs before
      the alias sync was seeded.
    - Accent-fold (Hawai'i → Hawaii, San José State → San Jose State)
    - Drop non-alphanumeric so 'St.' vs 'State' etc collapse.
    """
    if not name: return ''
    import unicodedata as _u
    n = _u.normalize('NFKD', name)
    n = ''.join(c for c in n if not _u.combining(c))
    n = n.lower().replace("'", '').replace('-', ' ')
    # 2026-09-06: canonical rewrite BEFORE mascot-strip so abbreviations
    # and renamed teams map to the canonical form used by CFBD/ESPN. Fixes
    # 4-of-9 Week 1 ungraded games (FIU, LIU Sharks, Youngstown St
    # Penguins, Houston Baptist Huskies renamed to Houston Christian).
    # Add here rather than in a separate lookup so the resolver stays
    # DB-independent.
    _NCAAF_ALIASES = {
        'fiu': 'florida international',
        'liu': 'long island university',
        'liu sharks': 'long island university sharks',
        'ulm': 'louisiana monroe',
        'ul monroe': 'louisiana monroe',
        'houston baptist': 'houston christian',
        'houston baptist huskies': 'houston christian huskies',
        'youngstown st': 'youngstown state',
        'youngstown st penguins': 'youngstown state penguins',
    }
    n = _NCAAF_ALIASES.get(n, n)
    # Strip mascot suffix words — order matters (multi-word first).
    # 2026-09-08 GAP EXPANSION: added remaining CFBD/odds-source mascot
    # suffixes discovered during 9/6 backfill audit. Each unresolved
    # game's mascot suffix now maps here. Multi-word entries MUST be
    # listed before their single-word tails so the loop matches longest
    # first (e.g. "ragin cajuns" before "cajuns").
    _MASCOTS = ['ragin cajuns', 'delta devils', 'red raiders', 'red wolves',
                'blue devils', 'blue raiders', 'golden bears', 'golden eagles',
                'golden hurricane', 'golden flashes', 'golden lions',
                'crimson tide', 'green wave', 'yellow jackets',
                'mountaineers', 'commodores', 'volunteers', 'razorbacks',
                'gamecocks', 'longhorns', 'bulldogs', 'wildcats', 'cardinals',
                'panthers', 'tigers', 'bearcats', 'buckeyes', 'wolverines',
                'nittany lions', 'fighting irish', 'hokies', 'demon deacons',
                'sun devils', 'utes', 'ducks', 'beavers', 'huskies', 'cougars',
                'trojans', 'bruins', 'aztecs', 'rebels', 'runnin rebels',
                'lobos', 'aggies', 'mustangs', 'horned frogs', 'red hawks',
                'chippewas', 'eagles', 'hornets', 'bison', 'rams', 'lions',
                'seahawks', 'hurricanes', 'gators', 'seminoles', 'canes',
                # 2026-09-08 expansions from 9/6 backfill audit
                'trailblazers', 'lakers', 'keydets', 'cajuns', 'colonels',
                'penguins', 'sharks', 'flames', 'roadrunners', 'phoenix',
                'coyotes', 'thundering herd', 'chanticleers', 'monarchs',
                'privateers', 'salukis', 'redbirds', 'redhawks', 'jaguars',
                'zips', 'minutemen', 'islanders', 'racers', 'sycamores',
                'leathernecks', 'antelopes', 'grizzlies', 'thunderbirds',
                'vandals', 'broncos', 'wolfpack', 'orange', 'catamounts',
                'mocs', 'moccasins', 'toreros', 'flyers', 'blazers',
                'boilermakers', 'hoosiers', 'terrapins', 'terps', 'irish',
                'blue hens', 'aggies', 'billikens', 'greyhounds',
                'thoroughbreds', 'braves', 'warriors', 'knights', 'raiders',
                'spartans', 'saints', 'pirates', 'bulls', 'bears',
                'redwolves', 'redwolves', 'catamounts', 'pioneers',
                'stallions', 'raptors', 'ospreys', 'condors', 'kangaroos',
                'sooners', 'cowboys', 'cyclones', 'jayhawks', 'hurricanes']
    for suf in _MASCOTS:
        if n.endswith(' ' + suf):
            n = n[:-(len(suf)+1)].strip()
            break
    import re as _re
    return _re.sub(r'[^a-z0-9]', '', n)


def fetch_existing(game_ids: list) -> dict:
    """Batch fetch existing rows by game_id. Returns {game_id: row}.

    2026-08-30: switched from `game_id=in.(...)` URL-embedded filter to a
    broad date-range pull + client-side filter. NCAAF game_ids from
    ncaaf_odds_pull embed team names with SPACES ('ncaaf_20260829_
    New Mexico State_Florida State'). Spaces don't URL-encode when
    embedded literally in the request URL string, so PostgREST returned
    0 rows silently — no NCAAF scores ever wrote back after the odds
    pull started using canonical team names. Fetch-by-date is boring
    but bulletproof.

    2026-08-30 (part 2): also emits a fuzzy index keyed by
    (date, _fold_name(away), _fold_name(home)) so mascot-suffixed rows
    from earlier ncaaf_odds_pull runs still match CFBD's canonical
    team names (Sacramento State Hornets → sacramentostate). Stored
    under the fuzzy key + the raw game_id key so the caller sees both.
    """
    if not game_ids: return {}
    # Extract unique YYYY-MM-DD dates from game_ids of the form
    # 'ncaaf_YYYYMMDD_...' + a cfbd_{id} bucket (dateless).
    dates = set()
    has_cfbd = False
    for gid in game_ids:
        if gid.startswith('cfbd_'):
            has_cfbd = True; continue
        if gid.startswith('ncaaf_') and len(gid) >= 14:
            ymd = gid[6:14]
            if ymd.isdigit():
                dates.add(f'{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}')
    wanted = set(game_ids)
    out = {}
    # 2026-09-08 GAP FIX: also fetch from ncaaf_game_context — that
    # table has broader coverage than ncaaf_game_results (which was
    # patch-only, hence sparse). ctx entries let the resolver INSERT
    # missing results rows so grade_jerry_reads has scores to work with.
    # See project_ncaaf_grading_gap_908 memory. Ctx rows are marked
    # `_source: 'ctx'` so caller knows to INSERT vs PATCH.
    for d in dates:
        # First pull existing results rows (PATCH targets)
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_game_results',
            headers=H_READ,
            params={'game_date': f'eq.{d}',
                    'select': 'game_id,home_team,away_team,home_score,'
                              'away_score,close_spread,close_total,'
                              'spread_result,total_result',
                    'limit': 1000},
            timeout=30,
        )
        if r.status_code == 200:
            for row in r.json() or []:
                gid = row.get('game_id')
                row['_source'] = 'results'
                if gid in wanted:
                    out[gid] = row
                fuzzy_key = (d, _fold_name(row.get('away_team') or ''),
                             _fold_name(row.get('home_team') or ''))
                out[fuzzy_key] = row
        # Then pull ctx rows for the same date (INSERT targets when
        # results doesn't have an entry yet). ctx has spread/total for
        # us to preserve when we insert results row.
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_game_context',
            headers=H_READ,
            params={'game_date': f'eq.{d}',
                    'select': 'game_id,home_team,away_team,close_spread,close_total',
                    'limit': 1000},
            timeout=30,
        )
        if r.status_code == 200:
            for row in r.json() or []:
                gid = row.get('game_id')
                # Only fill in from ctx if results didn't already have it
                if gid in wanted and gid not in out:
                    ctx_row = {**row, '_source': 'ctx',
                               'home_score': None, 'away_score': None}
                    out[gid] = ctx_row
                fuzzy_key = (d, _fold_name(row.get('away_team') or ''),
                             _fold_name(row.get('home_team') or ''))
                if fuzzy_key not in out:
                    out[fuzzy_key] = {**row, '_source': 'ctx',
                                      'home_score': None, 'away_score': None}
        # 2026-09-08 GAP FIX Phase 2: jerry_reads has the FULL slate of
        # game_ids we scored (70+ for Sat 9/6) while ctx has only 3.
        # Pull jerry_reads game_ids as third-tier coverage — same
        # game_id string matches against our constructed CFBD keys.
        # Rows sourced from jerry_reads have neither teams (in this
        # projection) nor spread/total, so INSERT payload has fewer
        # fields but still enough for grade_jerry_reads to resolve.
        r = requests.get(
            f'{SB}/rest/v1/jerry_reads',
            headers=H_READ,
            params={'sport': 'eq.NCAAF',
                    'game_date': f'eq.{d}',
                    'select': 'game_id',
                    'limit': 1000},
            timeout=30,
        )
        if r.status_code == 200:
            for row in r.json() or []:
                gid = row.get('game_id')
                if not gid: continue
                # Parse teams from game_id: ncaaf_YYYYMMDD_Away_Home
                parts = gid.split('_', 3)
                away = parts[2] if len(parts) >= 3 else None
                home = parts[3] if len(parts) >= 4 else None
                if not (away and home): continue
                jr_row = {
                    'game_id': gid, 'game_date': d,
                    'home_team': home, 'away_team': away,
                    'home_score': None, 'away_score': None,
                    'close_spread': None, 'close_total': None,
                    '_source': 'jerry_reads',
                }
                # Add both exact-string match AND fuzzy tuple match
                # (fuzzy_key catches CFBD names that differ from our
                # canonical, e.g. 'Miami' vs 'Miami (FL)').
                if gid in wanted and gid not in out:
                    out[gid] = jr_row
                fuzzy_key = (d, _fold_name(away), _fold_name(home))
                if fuzzy_key not in out:
                    out[fuzzy_key] = jr_row
    # Handle any cfbd_{id} keys (legacy rows) via a separate small query
    if has_cfbd:
        cfbd_ids = [g for g in game_ids if g.startswith('cfbd_')]
        for i in range(0, len(cfbd_ids), 100):
            chunk = cfbd_ids[i:i+100]
            r = requests.get(
                f'{SB}/rest/v1/ncaaf_game_results',
                headers=H_READ,
                params={'game_id': f'in.({",".join(chunk)})',
                        'select': 'game_id,home_score,away_score,close_spread,close_total',
                        'limit': 1000},
                timeout=30,
            )
            if r.status_code == 200:
                for row in r.json() or []:
                    row['_source'] = 'results'
                    out[row['game_id']] = row
    return out


def apply_patches(patches: list, dry_run: bool = False) -> int:
    """Apply score-refresh payloads.

    Each `patches` entry is (game_id, payload, existing_row). If
    existing_row was sourced from ncaaf_game_context ('_source': 'ctx'),
    we UPSERT (insert-or-update) via PostgREST's on_conflict param —
    that INSERTs a fresh ncaaf_game_results row so grade_jerry_reads
    can grade the game. If sourced from 'results', PATCH as before.

    2026-09-08 GAP FIX: was patch-only, so 84% of NCAAF Sat jerry_reads
    stayed ungraded because their game_ids had no matching results row
    for the resolver to patch. Now the resolver becomes authoritative
    source for ncaaf_game_results rows too.
    """
    if not patches: return 0
    if dry_run:
        for entry in patches[:10]:
            gid, payload, ex = entry
            src = ex.get('_source', 'results') if ex else 'unknown'
            op = 'UPSERT' if src in ('ctx', 'jerry_reads') else 'PATCH'
            print(f'  [DRY] {op} {gid}: {payload}')
        if len(patches) > 10:
            print(f'  [DRY] ... {len(patches)-10} more')
        return len(patches)
    updated = inserted = 0
    for entry in patches:
        gid, payload, ex = entry
        src = ex.get('_source', 'results') if ex else 'results'
        if src in ('ctx', 'jerry_reads'):
            # Full INSERT via UPSERT — merge with ctx metadata (teams,
            # spread, total) that the results row needs for grading.
            # `season` is NOT NULL on ncaaf_game_results — derive from
            # game_id date if possible. Same for game_date.
            game_date_str = (gid[6:14][:4] + '-' + gid[6:14][4:6] + '-' + gid[6:14][6:8]
                             if gid.startswith('ncaaf_') and len(gid) >= 14 and gid[6:14].isdigit()
                             else ex.get('game_date'))
            season = int(game_date_str[:4]) if game_date_str else None
            merged = {
                'game_id': gid,
                'season': season,
                'game_date': game_date_str,
                'season_type': 'regular',
                'home_team': ex.get('home_team'),
                'away_team': ex.get('away_team'),
                'close_spread': ex.get('close_spread'),
                'close_total': ex.get('close_total'),
                **payload,
            }
            merged = {k: v for k, v in merged.items() if v is not None or k in ('home_score','away_score')}
            r = requests.post(
                f'{SB}/rest/v1/ncaaf_game_results?on_conflict=game_id',
                headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
                json=merged, timeout=15,
            )
            if r.status_code in (200, 201, 204):
                inserted += 1
            else:
                print(f'  ⚠ upsert {gid} failed {r.status_code}: {r.text[:120]}')
        else:
            r = requests.patch(
                f'{SB}/rest/v1/ncaaf_game_results?game_id=eq.{gid}',
                headers=H_WRITE, json=payload, timeout=15,
            )
            if r.status_code in (200, 201, 204):
                updated += 1
            else:
                print(f'  ⚠ patch {gid} failed {r.status_code}: {r.text[:120]}')
    if inserted:
        print(f'  ✓ inserted {inserted} new ncaaf_game_results rows (gap-fill)')
    return updated + inserted


def _sweep_ungraded_jerry_reads(cfbd_games: list, dry_run: bool = False) -> int:
    """Reverse pass: for every ungraded NCAAF jerry_read, parse teams
    from its game_id and try to find a CFBD game with matching
    date + fuzzy-folded team pair. UPSERT a ncaaf_game_results row
    so grade_jerry_reads can pick up the score on its next run.

    This is the permanent fix for the alias-coverage gap. Instead of
    trying to alias-map all 454 CFBD games to our team names, we
    iterate the smaller set (games we actually need to grade) and
    directly compare fuzzy-folded names — which matches even when
    team_resolver doesn't know either name variant.

    Idempotent: skips jerry_reads whose game_ids already have a
    ncaaf_game_results row.
    """
    from collections import defaultdict
    # Pull ungraded NCAAF jerry_reads (up to last 30 days)
    r = requests.get(f'{SB}/rest/v1/jerry_reads',
                     headers=H_READ,
                     params={'sport': 'eq.NCAAF',
                             'result': 'is.null',
                             'select': 'game_id,game_date',
                             'order': 'game_date.desc',
                             'limit': 1500},
                     timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ jerry_reads fetch failed {r.status_code}')
        return 0
    jr_rows = r.json() or []
    if not jr_rows:
        return 0
    jr_by_date = defaultdict(set)
    for row in jr_rows:
        d = row.get('game_date'); gid = row.get('game_id')
        if d and gid: jr_by_date[d].add(gid)
    if not jr_by_date:
        return 0

    # Index CFBD games by date + (fold(away), fold(home))
    cfbd_by_date = defaultdict(dict)  # date -> {(a_fold, h_fold): game}
    for g in cfbd_games:
        kickoff = (g.get('start_date') or g.get('startDate')
                   or g.get('kickoff_utc') or '')
        d = kickoff[:10] if kickoff else ''
        if not d: continue
        home_pts = _i(g.get('home_points') or g.get('homePoints'))
        away_pts = _i(g.get('away_points') or g.get('awayPoints'))
        if home_pts is None or away_pts is None: continue
        home_raw = g.get('home_team') or g.get('homeTeam') or ''
        away_raw = g.get('away_team') or g.get('awayTeam') or ''
        cfbd_by_date[d][(_fold_name(away_raw), _fold_name(home_raw))] = g

    upserts = 0
    unresolved_samples = []
    dates_processed = 0
    total_ungraded = sum(len(v) for v in jr_by_date.values())
    for d, gids in jr_by_date.items():
        if d not in cfbd_by_date: continue
        dates_processed += 1
        cfbd_lookup = cfbd_by_date[d]
        # Batch-check existing ncaaf_game_results for these game_ids to
        # skip ones already covered by the earlier PATCH/UPSERT passes.
        existing_ids = set()
        gids_list = list(gids)
        for i in range(0, len(gids_list), 100):
            chunk = gids_list[i:i+100]
            ids_param = ','.join(f'"{g}"' for g in chunk)
            er = requests.get(f'{SB}/rest/v1/ncaaf_game_results',
                              headers=H_READ,
                              params={'game_id': f'in.({ids_param})',
                                      'select': 'game_id,home_score'},
                              timeout=30)
            if er.status_code == 200:
                for row in er.json() or []:
                    # Only skip if scores already populated
                    if row.get('home_score') is not None:
                        existing_ids.add(row.get('game_id'))
        for gid in gids:
            if gid in existing_ids: continue
            parts = gid.split('_', 3)
            if len(parts) < 4: continue
            away_raw = parts[2]; home_raw = parts[3]
            key = (_fold_name(away_raw), _fold_name(home_raw))
            cfbd_g = cfbd_lookup.get(key)
            if not cfbd_g:
                if len(unresolved_samples) < 6:
                    unresolved_samples.append(f'{away_raw} @ {home_raw} ({d})')
                continue
            home_pts = _i(cfbd_g.get('home_points') or cfbd_g.get('homePoints'))
            away_pts = _i(cfbd_g.get('away_points') or cfbd_g.get('awayPoints'))
            season = int(d[:4])
            payload = {
                'game_id': gid, 'season': season, 'season_type': 'regular',
                'game_date': d,
                'home_team': home_raw, 'away_team': away_raw,
                'home_score': home_pts, 'away_score': away_pts,
                'total_points': home_pts + away_pts,
                'home_win': home_pts > away_pts,
                'overtime': bool(cfbd_g.get('overtime')),
            }
            if dry_run:
                print(f'  [DRY] UPSERT (reverse) {gid}: {away_pts}-{home_pts}')
                upserts += 1
                continue
            r = requests.post(
                f'{SB}/rest/v1/ncaaf_game_results?on_conflict=game_id',
                headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
                json=payload, timeout=15,
            )
            if r.status_code in (200, 201, 204):
                upserts += 1
            else:
                print(f'  ⚠ reverse-upsert {gid} failed {r.status_code}: {r.text[:150]}')
    if unresolved_samples:
        print(f'  ⚠ still-unresolved (no CFBD fuzzy match): {len(unresolved_samples)}+ samples')
        for s in unresolved_samples:
            print(f'      · {s}')
    print(f'  reverse-sweep: {upserts} upserts from {total_ungraded} ungraded jerry_reads '
          f'across {dates_processed} dates')
    return upserts


def kick_external_resolver() -> None:
    """Fire the sport-agnostic external picks resolver for NCAAF."""
    print(f'\n=== Kicking off resolve_externals --sport NCAAF ===')
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resolve_externals.py')
    try:
        result = subprocess.run(
            [sys.executable, script, '--sport', 'NCAAF', '--days', '14'],
            capture_output=True, text=True, timeout=120,
        )
        # Show tail of output
        if result.stdout:
            print('\n'.join(result.stdout.splitlines()[-8:]))
    except Exception as e:
        print(f'  ⚠ external resolver kickoff failed: {e}')


def run(season: Optional[int] = None, dry_run: bool = False,
        skip_external: bool = False) -> None:
    if season is None:
        # Auto: current year if we're past July, else prior year
        now = datetime.now()
        season = now.year if now.month >= 7 else now.year - 1
    print(f'=== NCAAF resolver · season {season} ===')

    if not CFBD_KEY:
        print('  ✗ CFBD_API_KEY missing — abort')
        return

    cfbd_games = fetch_games_for_scoring(season)
    print(f'  CFBD games w/ scores: {len(cfbd_games)}')
    if not cfbd_games:
        return

    # 2026-08-09 fix: 2026 odds-pull rows use game_id format
    # `ncaaf_YYYYMMDD_<away>_<home>` (per ncaaf_odds_pull.py); historical rows
    # use `cfbd_{id}`. Resolver must look up BOTH keys so it grades ALL
    # game_id_map keys. If only cfbd_{id} was tried, resolver silently
    # updates zero games for 2026 season.
    def _slugify(name):
        import re as _re
        return _re.sub(r'[^a-z0-9]', '', (name or '').lower())

    # 2026-08-30 fix: ncaaf_odds_pull writes game_id as
    # 'ncaaf_YYYYMMDD_{away}_{home}' with team names AS-IS (spaces
    # preserved) via team_resolver canonical names. Prior resolver
    # only tried the slugified variant → 0 matches, no scores written,
    # NCAAF surface_records permanently blank. Try both variants +
    # also resolve CFBD's raw team names through our resolver to hit
    # the canonical name our odds pull actually stored.
    try:
        import sys as _sys
        from pathlib import Path as _P
        _sys.path.insert(0, str(_P(__file__).parent))
        from team_resolver import resolve_ncaaf_team
    except Exception:
        resolve_ncaaf_team = None

    def _key_variants_for(g):
        keys = []
        if g.get('id'):
            keys.append(f'cfbd_{g["id"]}')
        # CFBD returns camelCase (startDate, homeTeam) AND some fields
        # in snake_case depending on endpoint version. Try both.
        kickoff = (g.get('start_date') or g.get('startDate')
                   or g.get('kickoff_utc') or '')
        ymd = kickoff[:10].replace('-','') if kickoff else ''
        if not ymd: return keys
        away_raw = (g.get('away_team') or g.get('awayTeam') or '')
        home_raw = (g.get('home_team') or g.get('homeTeam') or '')
        # Variant A: slugified (legacy)
        keys.append(f'ncaaf_{ymd}_{_slugify(away_raw)}_{_slugify(home_raw)}')
        # Variant B: raw team names (matches ncaaf_odds_pull today —
        # canonical names from team_resolver preserve spaces)
        if away_raw and home_raw:
            keys.append(f'ncaaf_{ymd}_{away_raw}_{home_raw}')
        # Variant C: RESOLVER-CANONICAL names (covers cases where CFBD's
        # school field differs from our canonical, e.g. "Miami" vs "Miami (FL)")
        if resolve_ncaaf_team:
            can_a = resolve_ncaaf_team(away_raw) or away_raw
            can_h = resolve_ncaaf_team(home_raw) or home_raw
            if (can_a != away_raw) or (can_h != home_raw):
                keys.append(f'ncaaf_{ymd}_{can_a}_{can_h}')
        return keys

    key_variants = {}
    for g in cfbd_games:
        for k in _key_variants_for(g):
            if k: key_variants[k] = g
    all_keys = list(key_variants.keys())
    existing = fetch_existing(all_keys)
    # existing now includes both exact string keys AND fuzzy tuple keys
    # (date, fold(away), fold(home)) so mascot-suffixed rows still match.
    string_hits = sum(1 for k in existing if isinstance(k, str))
    print(f'  matched existing rows (exact): {string_hits}/{len(cfbd_games)} '
          f'(tried {len(all_keys)} key variants)')

    patches = []
    seen_ids = set()
    for gid, cfbd_g in key_variants.items():
        ex = existing.get(gid)
        if not ex: continue
        cid = cfbd_g.get('id')
        if cid in seen_ids: continue
        seen_ids.add(cid)
        payload = compute_outcome_patch(cfbd_g, ex)
        if payload:
            patches.append((ex['game_id'], payload, ex))

    # Fuzzy pass for any CFBD games not yet matched
    fuzzy_hits = 0
    for g in cfbd_games:
        cid = g.get('id')
        if cid in seen_ids: continue
        kickoff = (g.get('start_date') or g.get('startDate')
                   or g.get('kickoff_utc') or '')
        d = kickoff[:10] if kickoff else ''
        if not d: continue
        away_raw = (g.get('away_team') or g.get('awayTeam') or '')
        home_raw = (g.get('home_team') or g.get('homeTeam') or '')
        fuzzy_key = (d, _fold_name(away_raw), _fold_name(home_raw))
        ex = existing.get(fuzzy_key)
        if not ex: continue
        seen_ids.add(cid)
        fuzzy_hits += 1
        payload = compute_outcome_patch(g, ex)
        if payload:
            patches.append((ex['game_id'], payload, ex))
    if fuzzy_hits:
        print(f'  fuzzy-matched {fuzzy_hits} additional games via mascot-fold')

    updated = apply_patches(patches, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}applied {updated} score-refresh patches (of {len(patches)} candidates)')

    # 2026-09-08 PERMANENT GAP FIX (Phase 3): reverse-iterate from
    # jerry_reads. The CFBD-forward passes above catch games where
    # team_resolver knows the CFBD alias for our team name. But
    # jerry_reads has 70+ NCAAF game_ids per Saturday, and CFBD has
    # 454. When the intersection via aliases is only ~97 games, the
    # other 350 CFBD games silently drop scores that our jerry_reads
    # rows need for grading.
    #
    # This pass flips the direction: for every ungraded jerry_reads
    # game_id, parse teams from the ID, fuzzy-match against CFBD's
    # date+team_pair. This catches everything the alias table doesn't
    # know, since we're comparing fold(our_team) vs fold(cfbd_team)
    # directly. Any leftover unmatched jerry_read gets logged — that's
    # a real signal to add an alias.
    reverse_upserts = _sweep_ungraded_jerry_reads(cfbd_games, dry_run=dry_run)
    if reverse_upserts:
        print(f'{prefix}reverse-sweep from jerry_reads: '
              f'{reverse_upserts} game_results rows inserted')

    if not skip_external and not dry_run:
        kick_external_resolver()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-external', action='store_true',
                    help='Do not kick off resolve_externals afterward')
    args = ap.parse_args()
    run(season=args.season, dry_run=args.dry_run, skip_external=args.skip_external)


if __name__ == '__main__':
    main()
