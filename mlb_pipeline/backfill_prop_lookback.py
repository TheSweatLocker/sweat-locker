"""Backfill L5/L10 player-vs-line hit counts on today's props (2026-08-17).

Populates:
  * player_l5_hit_count   INT — games in last 5 hitting the prop_line
  * player_l10_hit_count  INT — games in last 10 hitting the prop_line
  * player_season_hit_pct NUMERIC — season rate at this line
  * player_l5_extreme_flag / player_l10_extreme_flag BOOL

These fields power the `player_l10_vs_line_extreme` playbook signal.

Data sources per sport:
  * MLB — pitcher_game_logs / batter_game_logs from Baseball Savant
  * NFL — nfl_player_game_logs (via nfl_data_py backfill)

Idempotent: PATCHes each prop row. Safe to re-run.

CLI:
  python backfill_prop_lookback.py                    # today, both sports
  python backfill_prop_lookback.py --sport MLB
  python backfill_prop_lookback.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

PROPS_TABLE = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',
    'NHL': 'nhl_pipeline_props',    # 2026-08-17: L10 lookback needs NHL player game log fetcher (TBD)
    'NBA': 'nba_pipeline_props',    # 2026-08-17: L10 lookback needs NBA player game log fetcher (TBD)
}

# Map prop_type → stat_field on player game logs
MLB_STAT_MAP = {
    'hits':  'hits',
    'ks':    'strikeouts',      # pitcher Ks
    'ha':    'hits_allowed',
    'bb':    'walks',           # pitcher BB — for batter BB, key differs
    'outs':  'outs',
    'er':    'earned_runs',
}
NFL_STAT_MAP = {
    # 2026-08-22 CRITICAL FIX (silent-bug audit finding #3): keys MUST match
    # what nfl_generate_props actually writes as prop_type. Prior version was
    # keyed by long names (`passing_yards`, `receiving_yards`) but the
    # production prop_types are short-form (`pass_yds`, `rush_yds`, etc.)
    # — see nfl_generate_props.py:417 `market_key.replace('player_', '')`.
    # Every NFL prop L10 lookup would silently return None the moment NFL
    # backfill enables. Latent launch-blocker until fixed.
    #
    # Keys here match the prop-type FAMILY (post strip of _over/_under suffix).
    'pass_yds':      'passing_yards',
    'rush_yds':      'rushing_yards',
    'reception_yds': 'receiving_yards',
    'receptions':    'receptions',
    'pass_tds':      'passing_tds',
    'pass_attempts': 'attempts',
    'pass_completions': 'completions',
    'ints':          'interceptions',
    'rush_attempts': 'carries',
    'anytime_td':    'td_any',
    'longest_reception':          'longest_reception',
    'longest_rush':               'longest_rush',
    'passing_longest_completion': 'longest_completion',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _mlb_stat_key(prop_type: str) -> str | None:
    """Convert prop_type ('hits_over','bb_under') to stat field name."""
    if not prop_type: return None
    base = prop_type.split('_')[0]  # 'hits_over' → 'hits'
    return MLB_STAT_MAP.get(base)


def _nfl_stat_key(prop_type: str) -> str | None:
    """Convert prop_type ('pass_yds_over','reception_yds_under') → stat field.
    2026-08-22: strip _over/_under suffix same as MLB path (finding #3)."""
    if not prop_type: return None
    base = prop_type
    for suffix in ('_over', '_under'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    return NFL_STAT_MAP.get(base)


_PLAYER_ID_CACHE: dict = {}


def _norm_name(name: str) -> str:
    """Strip diacritics (Jesús Luzardo → Jesus Luzardo) so MLB API lookups
    succeed for players whose names include accented chars in the sportsbook
    feed but not in the MLB Stats API index. 2026-08-22 fix: same pattern
    that was landed in grade_prop_playbook.py — apply here so L10 lookback
    doesn't silently return empty vals for accented names, contributing to
    the 25% L10 coverage gap."""
    if not name:
        return name
    import unicodedata
    return unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('ASCII')


def _mlb_player_id(player_name: str) -> Optional[int]:
    """Look up MLB player ID via statsapi.mlb.com (mirrors generate_props._lookup_player_id).

    Tries exact name first; if no match and the name has diacritics, retries
    with the ASCII-normalized form."""
    if player_name in _PLAYER_ID_CACHE:
        return _PLAYER_ID_CACHE[player_name]
    def _search(nm):
        try:
            r = requests.get('https://statsapi.mlb.com/api/v1/people/search',
                             params={'names': nm, 'active': 'true'},
                             timeout=8)
            return r.json().get('people', []) if r.status_code == 200 else []
        except Exception:
            return []
    people = _search(player_name)
    if not people:
        normalized = _norm_name(player_name)
        if normalized and normalized != player_name:
            people = _search(normalized)
    pid = people[0]['id'] if people else None
    _PLAYER_ID_CACHE[player_name] = pid
    return pid


# MLB Stats API stat field names.
# 2026-08-22 CRITICAL FIX: keys MUST match the values produced by MLB_STAT_MAP
# (line 52), NOT the prop_type base tokens. Prior version was keyed by
# `ks`/`ha`/`bb`/`er` but _mlb_stat_key returns the MAPPED value
# (`strikeouts`/`hits_allowed`/`walks`/`earned_runs`), so every pitcher prop
# lookup silently returned None → api_key = None → empty vals → L10 NULL.
# This bug broke ~75% of L10 coverage (only 'hits'/'outs' happened to have
# matching keys). Andrew Painter er_over 2.5 landed PRIME conv=94 with NULL
# L10 because of this — he actually has 18 games in 2026 gameLog.
_MLB_API_STAT = {
    # batter — hitting group
    'hits':         ('hitting', 'hits'),
    # pitcher — pitching group
    'strikeouts':   ('pitching', 'strikeOuts'),
    'hits_allowed': ('pitching', 'hits'),
    'walks':        ('pitching', 'baseOnBalls'),
    'outs':         ('pitching', 'outs'),
    'earned_runs':  ('pitching', 'earnedRuns'),
}


def fetch_mlb_player_recent(player_name: str, stat_field: str, n: int = 30,
                             season: int = 2026) -> list[float]:
    """Pull last N games of a stat for an MLB player via MLB Stats API gameLog.

    Uses same pattern as generate_props.fetch_batter_l7 — live fetch per player,
    cached in _PLAYER_ID_CACHE. Falls back to prior season if current season
    has <3 games (early season)."""
    pid = _mlb_player_id(player_name)
    if not pid: return []
    api_key = _MLB_API_STAT.get(stat_field)
    if not api_key: return []
    group, api_field = api_key

    def _pull(sn):
        try:
            r = requests.get(
                f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
                params={'stats': 'gameLog', 'group': group, 'season': sn},
                timeout=10,
            )
            splits = r.json().get('stats', []) if r.status_code == 200 else []
            games = splits[0].get('splits', []) if splits else []
        except Exception:
            games = []
        # Newest first
        games.sort(key=lambda g: g.get('date', ''), reverse=True)
        vals = []
        for g in games:
            stat_obj = g.get('stat', {})
            v = stat_obj.get(api_field)
            if v is None: continue
            try: vals.append(float(v))
            except (TypeError, ValueError): continue
        return vals

    vals = _pull(season)
    if len(vals) < 3 and season >= 2025:
        # Roll forward from prior season for early-season sparsity
        vals = vals + _pull(season - 1)
    return vals[:n]


def fetch_mlb_player_recent_rows(player_name: str, stat_field: str, n: int = 10,
                                  season: int = 2026) -> list[dict]:
    """2026-08-22: NEW — return per-game rows for ESPN-style stat table.
    Each row: {date, value, opponent, home_away, decision, ip (if pitcher)}.
    Used by render_prop_template.py to render the "last 10 games" table
    the user asked for after seeing conflicting prop convictions.
    """
    pid = _mlb_player_id(player_name)
    if not pid: return []
    api_key = _MLB_API_STAT.get(stat_field)
    if not api_key: return []
    group, api_field = api_key

    def _pull(sn):
        try:
            r = requests.get(
                f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
                params={'stats': 'gameLog', 'group': group, 'season': sn},
                timeout=10,
            )
            splits = r.json().get('stats', []) if r.status_code == 200 else []
            games = splits[0].get('splits', []) if splits else []
        except Exception:
            games = []
        games.sort(key=lambda g: g.get('date', ''), reverse=True)
        rows = []
        for g in games:
            stat_obj = g.get('stat', {}) or {}
            v = stat_obj.get(api_field)
            if v is None: continue
            try: val = float(v)
            except (TypeError, ValueError): continue
            opp = g.get('opponent', {}) or {}
            row = {
                'date': g.get('date', '')[:10],
                'value': val,
                'opp': opp.get('abbreviation') or opp.get('name', '')[:3],
                'home': g.get('isHome', True),
            }
            # Pitcher-specific: add IP and decision for context
            if group == 'pitching':
                row['ip'] = stat_obj.get('inningsPitched')
                if stat_obj.get('gamesStarted', 0):
                    row['gs'] = True
            rows.append(row)
        return rows

    rows = _pull(season)
    if len(rows) < 3 and season >= 2025:
        rows = rows + _pull(season - 1)
    return rows[:n]


def compute_lookback(recent_values: list[float], prop_line: float, direction: str) -> dict:
    """Given ordered recent-first values + prop line + direction,
    compute L5/L10 hit counts + extreme flags + season pct."""
    if not recent_values:
        return {'l5': None, 'l10': None, 'season_pct': None,
                'l5_extreme': None, 'l10_extreme': None}
    def _hits(vals, line, dir_):
        """Count games where the player HIT the direction (over/under)."""
        hits = 0
        for v in vals:
            if dir_ == 'over' and v >= line: hits += 1
            elif dir_ == 'under' and v < line: hits += 1
        return hits
    l5 = _hits(recent_values[:5], prop_line, direction)
    l10 = _hits(recent_values[:10], prop_line, direction)
    all_hits = _hits(recent_values, prop_line, direction)
    season_pct = round(100.0 * all_hits / len(recent_values), 1)
    l5_extreme  = l5 >= 4 or l5 <= 1
    l10_extreme = l10 >= 8 or l10 <= 2
    return {'l5': l5, 'l10': l10, 'season_pct': season_pct,
            'l5_extreme': l5_extreme, 'l10_extreme': l10_extreme}


def backfill_mlb(game_date: str, dry_run: bool = False) -> int:
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             # 2026-08-23 CRITICAL FIX: `signals` was missing
                             # from SELECT. Backfill then did
                             # `existing_signals = prop.get('signals') or {}`
                             # which always returned {}, and the PATCH wiped
                             # every fired-signal key written by
                             # generate_props / juice_trap_gate / prop_refit.
                             # 100% of 8/22 + 8/23 props had EMPTY signals
                             # dicts as a result — hollow "Why UNDER/OVER"
                             # sections + uniform fake refit values per
                             # family (48.5/37.7/etc). Introduced in c922b2af.
                             'select': 'id,player_name,prop_type,direction,prop_line,signals',
                             'limit': '500'},
                     timeout=30)
    props = r.json() if r.status_code == 200 else []
    if not props:
        print(f'  MLB: no props on {game_date}'); return 0

    now_iso = datetime.now(timezone.utc).isoformat()
    updated = 0
    # Cache player recent-values so we only fetch each player once
    # 2026-08-22: also cache per-game rows for ESPN-table rendering
    recent_cache: dict[tuple, list[float]] = {}
    rows_cache: dict[tuple, list[dict]] = {}
    for prop in props:
        pname = prop.get('player_name')
        ptype = prop.get('prop_type')
        pdir  = prop.get('direction')
        pline = prop.get('prop_line')
        if not (pname and ptype and pdir is not None and pline is not None):
            continue
        stat = _mlb_stat_key(ptype)
        if not stat: continue
        try: pline = float(pline)
        except (TypeError, ValueError): continue

        cache_key = (pname, stat)
        if cache_key not in recent_cache:
            # 2026-08-22: wrap fetches too — MLB Stats API also 502/reset
            # occasionally. Same fail-loud-continue pattern as the patch.
            try:
                recent_cache[cache_key] = fetch_mlb_player_recent(pname, stat, n=30)
                rows_cache[cache_key] = fetch_mlb_player_recent_rows(pname, stat, n=10)
            except requests.exceptions.RequestException as _e:
                print(f'    ⚠ fetch failed for {pname}/{stat}: {_e}')
                recent_cache[cache_key] = []
                rows_cache[cache_key] = []
        recent = recent_cache[cache_key]
        rows_last10 = rows_cache.get(cache_key, [])

        lb = compute_lookback(recent, pline, pdir)
        if lb['l10'] is None: continue

        # 2026-08-22: merge raw per-game values into signals dict for
        # ESPN-table rendering downstream. Underscore-prefix marks it
        # as internal metadata (not shown as a signal chip). Template
        # renderer reads this to build the "last 10 starts" table user
        # asked for. Preserves existing signals — read-modify-write.
        existing_signals = prop.get('signals') or {}
        if not isinstance(existing_signals, dict): existing_signals = {}
        existing_signals['_stat_last10'] = rows_last10
        existing_signals['_stat_avg_l5'] = round(sum(recent[:5])/len(recent[:5]), 2) if recent[:5] else None
        existing_signals['_stat_avg_l10'] = round(sum(recent[:10])/len(recent[:10]), 2) if recent[:10] else None
        existing_signals['_stat_avg_season'] = round(sum(recent)/len(recent), 2) if recent else None
        existing_signals['_stat_field'] = stat
        existing_signals['_line'] = pline
        existing_signals['_direction'] = pdir

        patch = {
            'player_l5_hit_count':   lb['l5'],
            'player_l10_hit_count':  lb['l10'],
            'player_season_hit_pct': lb['season_pct'],
            'player_l5_extreme_flag':  lb['l5_extreme'],
            'player_l10_extreme_flag': lb['l10_extreme'],
            'player_lookback_updated_at': now_iso,
            'signals': existing_signals,
        }
        # 2026-08-27 batter_hits L10 gate: per user directive, hits_over 0.5
        # only publishes when the player is 10/10 in L10. Any hits_over row
        # with L10 < 10 gets tier=COVERAGE + conviction=0 so the Sharp Card
        # filter (tier in PRIME/STRONG) drops it. Grading still runs since
        # the row stays in the table — we just never show it or count it.
        # Root cause: batter_hits O 0.5 at -200+ juice hits ~61% but needs
        # 66%+ to break even. L10=10 filter targets the true-lock subset.
        prop_type = prop.get('prop_type', '')
        direction = prop.get('direction', '')
        if prop_type == 'hits_over' and direction == 'over':
            if lb['l10'] is None or int(lb['l10']) < 10:
                cur_tier = (prop.get('tier') or '').upper()
                if cur_tier in ('PRIME', 'STRONG', 'LEAN'):
                    patch['tier'] = 'COVERAGE'
                    patch['conviction'] = 0
        if not dry_run:
            # 2026-08-22 RETRY on transient ConnectionResetError. Prior
            # behavior: single failure killed the whole run — one prop's
            # patch throwing ConnectionError propagated up and stopped
            # processing all remaining props. Today's slate: crashed at
            # prop ~58 of 155, leaving 97 props with l10 counts but no
            # _stat_last10 (so the recent-form table wouldn't render on
            # those cards). Now: retry up to 3x with backoff, skip the
            # single prop if all retries fail, keep processing the rest.
            import time as _time
            pr = None
            for attempt in range(3):
                try:
                    pr = requests.patch(
                        f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                        headers=H_WRITE, json=patch, timeout=15,
                    )
                    break  # got a response (any code) — exit retry loop
                except requests.exceptions.RequestException as _e:
                    if attempt < 2:
                        _time.sleep(0.5 * (attempt + 1))
                        continue
                    print(f'    ✗ patch {prop["id"]} net error after 3 retries: {_e}')
                    pr = None
            if pr is None:
                continue
            if pr.status_code not in (200, 204):
                print(f'    ✗ patch {prop["id"]} failed: {pr.status_code}')
                continue
        updated += 1
        if lb['l10_extreme']:
            marker = '🔥' if lb['l10'] >= 8 else '🧊'
            print(f'  {marker} {pname:<22} {ptype:<10} {pdir:<5} line={pline}  L10={lb["l10"]}/10  L5={lb["l5"]}/5')
    return updated


def run(game_date: str | None = None, sport: str | None = None, dry_run: bool = False):
    gd = game_date or _et_today()
    sports = [sport] if sport else list(PROPS_TABLE.keys())
    print(f'=== backfill prop lookback · {gd} · {"/".join(sports)}{" [DRY]" if dry_run else ""} ===')
    for s in sports:
        if s == 'MLB':
            n = backfill_mlb(gd, dry_run=dry_run)
            print(f'  MLB: updated {n} props with lookback\n')
        elif s == 'NFL':
            print(f'  NFL: skipped — nfl_player_game_logs backfill not yet built\n')
        elif s == 'NHL':
            n = backfill_nba_nhl(gd, sport='NHL', dry_run=dry_run)
            print(f'  NHL: updated {n} props with lookback\n')
        elif s == 'NBA':
            n = backfill_nba_nhl(gd, sport='NBA', dry_run=dry_run)
            print(f'  NBA: updated {n} props with lookback\n')


# ─── NBA / NHL backfill via ESPN game-log endpoints ────────────────────
# ESPN /apis/site/v2/sports/{sport}/{league}/athletes/{id}/gamelog
# Free, no key needed. Player ID resolved via ESPN search.
# Same shape as MLB backfill: last 30 → compute L5/L10/season hit counts.

_NBA_STAT_KEY = {  # prop_type family → ESPN stat name
    'points':      'points',
    'pts':         'points',
    'rebounds':    'rebounds',
    'reb':         'rebounds',
    'assists':     'assists',
    'ast':         'assists',
    'threes':      'threePointFieldGoalsMade',
    'threes_made': 'threePointFieldGoalsMade',
    'steals':      'steals',
    'stl':         'steals',
    'blocks':      'blocks',
    'blk':         'blocks',
    'pra':         '_composite_pra',   # points + rebounds + assists
    'pr':          '_composite_pr',    # points + rebounds
    'pa':          '_composite_pa',    # points + assists
}
_NHL_STAT_KEY = {
    'sog':          'shotsOnGoal',
    'shots':        'shotsOnGoal',
    'points':       'points',
    'goals':        'goals',
    'assists':      'assists',
    'saves':        'saves',
    'blocks':       'blocks',
    'blk':          'blocks',
    'hits':         'hits',
}

_NBA_ESPN_SPORT = ('basketball', 'nba')
_NHL_ESPN_SPORT = ('hockey', 'nhl')


def _espn_player_id(sport_tuple: tuple, player_name: str) -> Optional[int]:
    """ESPN search API (public). Returns first athlete id or None."""
    if not player_name: return None
    try:
        q = requests.utils.quote(player_name)
        # ESPN's search endpoint — league-specific
        sport, league = sport_tuple
        r = requests.get(
            f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/athletes',
            params={'search': player_name, 'limit': 5}, timeout=10)
        if r.status_code != 200: return None
        for a in (r.json() or {}).get('athletes', []):
            if a.get('displayName', '').lower() == player_name.lower():
                return a.get('id')
        # Fall back to first result
        athletes = (r.json() or {}).get('athletes', [])
        return athletes[0].get('id') if athletes else None
    except Exception:
        return None


def _espn_gamelog(sport_tuple: tuple, player_id: int, n: int = 30) -> list:
    """Return raw ESPN gamelog events (list of dicts)."""
    sport, league = sport_tuple
    try:
        r = requests.get(
            f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/athletes/{player_id}/gamelog',
            timeout=15)
        if r.status_code != 200: return []
        data = r.json() or {}
        events = []
        for season in (data.get('seasonTypes') or []):
            for cat in (season.get('categories') or []):
                for ev in (cat.get('events') or []):
                    events.append(ev)
        return events[:n]
    except Exception:
        return []


def _extract_stat(event: dict, sport: str, stat_field: str, stat_labels: list) -> Optional[float]:
    """Pull a numeric stat from an ESPN gamelog event, matching stat label."""
    # Composite calcs
    if stat_field == '_composite_pra':
        p = _extract_stat(event, sport, 'points', stat_labels)
        r_ = _extract_stat(event, sport, 'rebounds', stat_labels)
        a = _extract_stat(event, sport, 'assists', stat_labels)
        if None in (p, r_, a): return None
        return p + r_ + a
    if stat_field == '_composite_pr':
        p = _extract_stat(event, sport, 'points', stat_labels)
        r_ = _extract_stat(event, sport, 'rebounds', stat_labels)
        return (p + r_) if p is not None and r_ is not None else None
    if stat_field == '_composite_pa':
        p = _extract_stat(event, sport, 'points', stat_labels)
        a = _extract_stat(event, sport, 'assists', stat_labels)
        return (p + a) if p is not None and a is not None else None
    # ESPN stats come as parallel arrays keyed by labels
    stats = event.get('stats') or []
    label_lower = [str(l).lower() for l in stat_labels]
    try:
        idx = label_lower.index(stat_field.lower())
    except ValueError:
        return None
    if idx < len(stats):
        try: return float(stats[idx])
        except (TypeError, ValueError): return None
    return None


def backfill_nba_nhl(game_date: str, sport: str, dry_run: bool = False) -> int:
    """NBA/NHL backfill: fetch ESPN game logs per player, compute L5/L10/season
    hit counts vs each prop line, patch back to *_pipeline_props."""
    table = PROPS_TABLE.get(sport)
    if not table:
        print(f'  {sport}: no PROPS_TABLE mapping'); return 0
    sport_tuple = _NBA_ESPN_SPORT if sport == 'NBA' else _NHL_ESPN_SPORT
    stat_map = _NBA_STAT_KEY if sport == 'NBA' else _NHL_STAT_KEY

    r = requests.get(f'{SB}/rest/v1/{table}',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'select': 'id,player_name,prop_type,direction,prop_line',
                             'limit': '500'}, timeout=30)
    props = r.json() if r.status_code == 200 else []
    if not props:
        print(f'  {sport}: no props on {game_date}'); return 0

    now_iso = datetime.now(timezone.utc).isoformat()
    recent_cache: dict = {}   # (pname, stat_field) → list[float]
    labels_cache: dict = {}   # player_id → stat labels list (per ESPN response)
    updated = 0
    for prop in props:
        pname = prop.get('player_name')
        ptype = prop.get('prop_type', '')
        pdir  = prop.get('direction')
        pline = prop.get('prop_line')
        if not (pname and ptype and pdir is not None and pline is not None): continue
        # Strip _over/_under suffix
        base = ptype
        for suf in ('_over', '_under'):
            if base.endswith(suf): base = base[:-len(suf)]; break
        stat_field = stat_map.get(base)
        if not stat_field: continue
        try: pline = float(pline)
        except (TypeError, ValueError): continue

        # Try exact + normalized name lookup
        cache_key = (pname, stat_field)
        if cache_key not in recent_cache:
            pid = _espn_player_id(sport_tuple, pname)
            if not pid:
                nn = _norm_name(pname)
                if nn and nn != pname:
                    pid = _espn_player_id(sport_tuple, nn)
            if not pid:
                recent_cache[cache_key] = []
            else:
                events = _espn_gamelog(sport_tuple, pid, n=30)
                if pid not in labels_cache and events:
                    # ESPN puts labels on categories — try first event's stat labels
                    labels_cache[pid] = events[0].get('_labels') or ['points','rebounds','assists']
                labels = labels_cache.get(pid, [])
                vals = []
                for ev in events:
                    v = _extract_stat(ev, sport, stat_field, labels)
                    if v is not None: vals.append(v)
                recent_cache[cache_key] = vals

        recent = recent_cache[cache_key]
        if not recent: continue
        lb = compute_lookback(recent, pline, pdir)
        if lb['l10'] is None: continue

        patch = {
            'player_l5_hit_count':   lb['l5'],
            'player_l10_hit_count':  lb['l10'],
            'player_season_hit_pct': lb['season_pct'],
            'player_l5_extreme_flag':  lb['l5_extreme'],
            'player_l10_extreme_flag': lb['l10_extreme'],
            'player_lookback_updated_at': now_iso,
        }
        if not dry_run:
            pr = requests.patch(f'{SB}/rest/v1/{table}?id=eq.{prop["id"]}',
                                headers=H_WRITE, json=patch, timeout=10)
            if pr.status_code not in (200, 204): continue
        updated += 1
    return updated


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(PROPS_TABLE.keys()))
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, sport=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
