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
    # NFL props — matches nfl_data_py per-game log fields (2026-08-17).
    # Applies to prop_type BASE (strip _over/_under suffix in fetcher).
    'passing_yards':   'passing_yards',
    'rushing_yards':   'rushing_yards',
    'receiving_yards': 'receiving_yards',
    'receptions':      'receptions',
    'passing_tds':     'passing_tds',
    'passing_completions': 'completions',
    'passing_attempts':    'attempts',
    'passing_interceptions': 'interceptions',
    'rushing_attempts':    'carries',
    'anytime_td':          'td_any',   # combined rushing_tds + receiving_tds + passing_tds (rush + rec for skill)
    'longest_reception':   'longest_reception',
    'longest_rush':        'longest_rush',
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
    return NFL_STAT_MAP.get(prop_type)


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


# MLB Stats API stat field names per prop_type base
_MLB_API_STAT = {
    # batter — hitting group
    'hits':  ('hitting', 'hits'),
    # pitcher — pitching group
    'ks':    ('pitching', 'strikeOuts'),
    'ha':    ('pitching', 'hits'),
    'bb':    ('pitching', 'baseOnBalls'),
    'outs':  ('pitching', 'outs'),
    'er':    ('pitching', 'earnedRuns'),
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
                             'select': 'id,player_name,prop_type,direction,prop_line',
                             'limit': '500'},
                     timeout=30)
    props = r.json() if r.status_code == 200 else []
    if not props:
        print(f'  MLB: no props on {game_date}'); return 0

    now_iso = datetime.now(timezone.utc).isoformat()
    updated = 0
    # Cache player recent-values so we only fetch each player once
    recent_cache: dict[tuple, list[float]] = {}
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
            recent_cache[cache_key] = fetch_mlb_player_recent(pname, stat, n=30)
        recent = recent_cache[cache_key]

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
            pr = requests.patch(f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                                headers=H_WRITE, json=patch, timeout=10)
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
            print(f'  NHL: skipped — NHL player game log fetcher not yet built. '
                  f'Playbook still fires other 4 NHL signals (goalie GSAA, pace, juice_trap, legacy_conviction).\n')
        elif s == 'NBA':
            print(f'  NBA: skipped — NBA player game log fetcher not yet built. '
                  f'Playbook fires 5 other NBA signals PLUS Sleeper projections '
                  f'(enrich_nba_prop_projections.py).\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(PROPS_TABLE.keys()))
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, sport=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
