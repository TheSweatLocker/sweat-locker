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
    if existing_home == home_score and existing_away == away_score:
        return None  # no change

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
    for d in dates:
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_game_results',
            headers=H_READ,
            params={'game_date': f'eq.{d}',
                    'select': 'game_id,home_score,away_score,close_spread,close_total',
                    'limit': 1000},
            timeout=30,
        )
        if r.status_code == 200:
            for row in r.json() or []:
                if row.get('game_id') in wanted:
                    out[row['game_id']] = row
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
                    out[row['game_id']] = row
    return out


def apply_patches(patches: list, dry_run: bool = False) -> int:
    if not patches: return 0
    if dry_run:
        for gid, payload in patches[:10]:
            print(f'  [DRY] {gid}: {payload}')
        if len(patches) > 10:
            print(f'  [DRY] ... {len(patches)-10} more')
        return len(patches)
    updated = 0
    for gid, payload in patches:
        r = requests.patch(
            f'{SB}/rest/v1/ncaaf_game_results?game_id=eq.{gid}',
            headers=H_WRITE, json=payload, timeout=15,
        )
        if r.status_code in (200, 201, 204):
            updated += 1
        else:
            print(f'  ⚠ patch {gid} failed {r.status_code}: {r.text[:120]}')
    return updated


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
    print(f'  matched existing rows: {len(existing)}/{len(cfbd_games)} '
          f'(tried {len(all_keys)} key variants)')

    patches = []
    seen_ids = set()
    for gid, cfbd_g in key_variants.items():
        ex = existing.get(gid)
        if not ex: continue
        # Avoid double-patching same CFBD game via both key variants
        cid = cfbd_g.get('id')
        if cid in seen_ids: continue
        seen_ids.add(cid)
        payload = compute_outcome_patch(cfbd_g, ex)
        if payload:
            patches.append((gid, payload))

    updated = apply_patches(patches, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}applied {updated} score-refresh patches (of {len(patches)} candidates)')

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
