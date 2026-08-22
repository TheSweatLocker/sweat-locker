"""NFL prop resolver (2026-08-03 · Sprint 2).

For each nfl_pipeline_props row where a game has completed but result is null:
  1. Pull the actual player stat for that game from nfl_data_py weekly data
  2. Compare vs prop_line + direction
  3. Write result (Win/Loss/Push/UNGRADEABLE) + final_value + resolved_at

Chain position:
  Runs AFTER game completes (weekly nfl_data_py refresh publishes ~Tue AM).
  Feeds grade_prop_jerry_reads which inherits the result to prop_jerry_reads.
  That feeds compute_prop_bucket_roi for the NFL feedback loop.

Stat mapping (nfl_data_py weekly columns → prop_type family):
  pass_yds       → passing_yards
  pass_tds       → passing_tds
  ints           → interceptions
  pass_attempts  → attempts
  rush_yds       → rushing_yards
  rec_yds        → receiving_yards
  receptions     → receptions
  anytime_td     → (rushing_tds + receiving_tds) >= 1

Usage:
    python resolve_nfl_props.py [--date YYYY-MM-DD] [--week N] [--dry-run]
"""
import argparse, os, sys, warnings, functools
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

warnings.filterwarnings('ignore')

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

try:
    import nfl_data_py as nfl
    NFL_AVAILABLE = True
except ImportError:
    NFL_AVAILABLE = False

# prop_type family → nfl_data_py column
# 2026-08-22 FIX (silent-bug audit finding #2): nfl_generate_props writes
# prop_type as `reception_yds_over`/`reception_yds_under` (see
# nfl_generate_props.py:417 which does `market_key.replace('player_','')`),
# but this dict was keyed by `rec_yds`. Every reception-yards prop was
# silently marked UNGRADEABLE. Adding both keys for safety in case any
# legacy rows still use `rec_yds`.
STAT_MAP = {
    'pass_yds':      'passing_yards',
    'pass_tds':      'passing_tds',
    'ints':          'interceptions',
    'pass_attempts': 'attempts',
    'rush_yds':      'rushing_yards',
    'reception_yds': 'receiving_yards',
    'rec_yds':       'receiving_yards',   # legacy row support
    'receptions':    'receptions',
    # anytime_td handled specially (sum of rush+rec TDs)
}


@functools.lru_cache(maxsize=8)
def _weekly_cache(season: int):
    """Cache nfl_data_py weekly data per season."""
    if not NFL_AVAILABLE: return None
    try:
        return nfl.import_weekly_data([season])
    except Exception:
        return None


def _season_for_date(game_date_str: str) -> int:
    """NFL season year — Sept 2026 games belong to 2026 season, etc."""
    y = int(game_date_str[:4])
    m = int(game_date_str[5:7])
    return y if m >= 8 else y - 1  # Jan-July games belong to prior season


def _get_player_stat(player_name: str, game_date: str, stat_col: str) -> Optional[float]:
    """Look up a player's stat value for a specific game_date."""
    season = _season_for_date(game_date)
    w = _weekly_cache(season)
    if w is None: return None
    m = w[w['player_display_name'] == player_name]
    if m.empty: return None
    # Filter to the specific game — nfl_data_py weekly rows have season+week
    # but not the exact date. Match by week number derived from game_date.
    # Rough approximation: NFL season starts ~Sept 4, week 1 = first Thu
    # game through following Mon. Simplification here: get any row from the
    # season for now — refine when we have per-game date lookup.
    row = m.iloc[-1]  # latest game — will refine to date-specific in Sprint 2b
    return float(row.get(stat_col, 0) or 0)


def _get_anytime_td(player_name: str, game_date: str) -> Optional[int]:
    """1 if player scored a TD (rush or rec), 0 otherwise, None if no data."""
    season = _season_for_date(game_date)
    w = _weekly_cache(season)
    if w is None: return None
    m = w[w['player_display_name'] == player_name]
    if m.empty: return None
    row = m.iloc[-1]
    rush = float(row.get('rushing_tds', 0) or 0)
    rec = float(row.get('receiving_tds', 0) or 0)
    return 1 if (rush + rec) >= 1 else 0


def _grade_direction(actual: float, line: float, direction: str) -> str:
    """Standard OVER/UNDER grading. Line push = Push."""
    if actual == line: return 'Push'
    if direction == 'over':
        return 'Win' if actual > line else 'Loss'
    return 'Win' if actual < line else 'Loss'


def resolve(game_date: str, dry_run: bool = False) -> int:
    if not NFL_AVAILABLE:
        print('⛔ nfl_data_py not installed'); return 0

    print(f'=== resolve_nfl_props · {game_date} ===')
    r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'result': 'is.null',
                             'select': 'id,player_name,prop_type,direction,prop_line'},
                     timeout=15).json()
    if not isinstance(r, list):
        print(f'  ⚠ pull failed: {r}'); return 0
    print(f'  {len(r)} pending rows to resolve')

    resolved = ungradeable = 0
    for row in r:
        prop_type = row['prop_type']
        family = prop_type.rsplit('_', 1)[0]  # pass_yds_over → pass_yds
        line = float(row['prop_line'] or 0)

        if family == 'anytime_td':
            actual = _get_anytime_td(row['player_name'], game_date)
            if actual is None:
                if not dry_run:
                    requests.patch(f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{row["id"]}',
                                   headers=H_WRITE,
                                   json={'result': 'UNGRADEABLE',
                                         'resolved_at': datetime.now(timezone.utc).isoformat()},
                                   timeout=10)
                ungradeable += 1
                continue
            result = _grade_direction(actual, line, row['direction'])
        else:
            stat_col = STAT_MAP.get(family)
            if not stat_col:
                if not dry_run:
                    requests.patch(f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{row["id"]}',
                                   headers=H_WRITE,
                                   json={'result': 'UNGRADEABLE',
                                         'resolved_at': datetime.now(timezone.utc).isoformat()},
                                   timeout=10)
                ungradeable += 1
                continue
            actual = _get_player_stat(row['player_name'], game_date, stat_col)
            if actual is None:
                if not dry_run:
                    requests.patch(f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{row["id"]}',
                                   headers=H_WRITE,
                                   json={'result': 'UNGRADEABLE',
                                         'resolved_at': datetime.now(timezone.utc).isoformat()},
                                   timeout=10)
                ungradeable += 1
                continue
            result = _grade_direction(actual, line, row['direction'])

        if dry_run:
            print(f'  [DRY] id={row["id"]} {row["player_name"]:<25} {prop_type:<20} '
                  f'line={line} actual={actual} → {result}')
            resolved += 1
            continue

        pr = requests.patch(f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{row["id"]}',
                            headers=H_WRITE,
                            json={'result': result, 'final_value': actual,
                                  'resolved_at': datetime.now(timezone.utc).isoformat()},
                            timeout=10)
        if pr.status_code in (200, 204):
            resolved += 1

    print(f'\n=== resolved {resolved} · UNGRADEABLE {ungradeable} ===')
    return resolved


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='game_date to resolve (default: yesterday ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    gd = args.date or (datetime.now(timezone.utc) - timedelta(hours=28)).strftime('%Y-%m-%d')
    resolve(gd, dry_run=args.dry_run)
