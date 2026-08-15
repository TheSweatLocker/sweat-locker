"""freeze_closing_lines — snapshot the TRUE closing line at T-Xmin per game.

Distinguishes "close_total was overwritten by whatever pull ran most
recently" from "close_total is the actual line at first pitch minus X min."
Writes `close_locked_at` timestamp when the freeze succeeds; downstream
code can trust that a non-null close_locked_at means the line is truly
frozen. Sport-universal via line_movement_config.

Per-sport freeze offset:
  MLB    T-5min   (fast market, first pitch is exact)
  NFL    T-15min  (kickoff can slide, market thicker)
  NCAAF  T-15min
  NCAAB  T-10min
  NHL    T-10min
  UFC    T-30min  (main event start time drifts)

Runs on a fast cron (every 5-10 min) — cheap operation; loops per game
and only writes when the game's freeze window hits AND close_locked_at
is still null (idempotent).

CLI
  python freeze_closing_lines.py                    # all sports
  python freeze_closing_lines.py --sport MLB
  python freeze_closing_lines.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
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
           'Prefer': 'return=minimal'}

from line_movement_config import get_config

SPORT_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NCAAB': 'ncaab_game_context',
    'NHL':   'nhl_game_context',
    # UFC — separate schema (per-fight); freeze handled by ufc_odds_pull directly
}

# Field name mapping per sport (schemas differ slightly)
SPORT_FIELDS = {
    'MLB':   {'commence_col': 'game_time_utc',
              'close_cols': ['close_total', 'close_spread', 'home_ml_close', 'away_ml_close']},
    'NFL':   {'commence_col': 'kickoff_utc',
              'close_cols': ['close_total', 'close_spread', 'close_home_ml', 'close_away_ml']},
    'NCAAF': {'commence_col': 'kickoff_utc',
              'close_cols': ['close_total', 'close_spread', 'close_home_ml', 'close_away_ml']},
    'NCAAB': {'commence_col': 'tipoff_utc',
              'close_cols': ['close_total', 'close_spread', 'close_home_ml', 'close_away_ml']},
    'NHL':   {'commence_col': 'puck_drop_utc',
              'close_cols': ['close_total', 'close_spread', 'close_home_ml', 'close_away_ml']},
}


def fetch_upcoming_games(sport: str, window_hrs: int = 2) -> list:
    """Return today's games not yet close-frozen and starting within `window_hrs`."""
    tbl = SPORT_TABLE.get(sport)
    if not tbl: return []
    fields = SPORT_FIELDS[sport]
    commence_col = fields['commence_col']
    close_cols   = fields['close_cols']

    now = datetime.now(timezone.utc)
    window_end = (now + timedelta(hours=window_hrs)).isoformat()
    select_cols = ','.join(['game_id', commence_col, 'close_locked_at'] + close_cols)

    r = requests.get(
        f'{SB}/rest/v1/{tbl}?select={select_cols}'
        f'&close_locked_at=is.null'
        f'&{commence_col}=lte.{window_end}'
        f'&{commence_col}=gte.{now.isoformat()}',
        headers=H_READ, timeout=20)
    if r.status_code != 200:
        # Column might not exist on this sport yet — non-fatal
        return []
    return r.json() or []


def freeze_game(sport: str, game: dict, dry_run: bool = False) -> bool:
    """Stamp close_locked_at (the current close_* values are the freeze)."""
    tbl = SPORT_TABLE[sport]
    gid = game['game_id']
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {'close_locked_at': now_iso}
    if dry_run:
        return True
    r = requests.patch(
        f'{SB}/rest/v1/{tbl}?game_id=eq.{gid}',
        headers=H_WRITE, json=payload, timeout=15)
    return r.status_code in (200, 204)


def run_sport(sport: str, dry_run: bool = False) -> tuple:
    if sport not in SPORT_TABLE:
        print(f'  {sport}: skipped (no game_context table)')
        return (0, 0)
    cfg = get_config(sport)
    close_offset_min = cfg['close_offset_min']

    commence_col = SPORT_FIELDS[sport]['commence_col']
    now = datetime.now(timezone.utc)

    # Look 2h ahead — a game due to start in <= close_offset_min is ready to freeze
    games = fetch_upcoming_games(sport, window_hrs=2)
    frozen = 0
    considered = 0
    for game in games:
        commence_str = game.get(commence_col)
        if not commence_str: continue
        try:
            commence = datetime.fromisoformat(commence_str.replace('Z', '+00:00'))
        except ValueError:
            continue
        # Freeze if we're within `close_offset_min` of first pitch/kickoff/tip
        mins_until = (commence - now).total_seconds() / 60.0
        if mins_until > close_offset_min:
            continue  # too early
        if mins_until < -30:
            continue  # game already started 30+ min ago, missed window
        considered += 1
        # Snapshot: leave existing close_* alone (they're set by latest odds pull);
        # just stamp close_locked_at so downstream knows this is the TRUE close.
        if freeze_game(sport, game, dry_run=dry_run):
            frozen += 1
            if dry_run:
                print(f'    [DRY] {sport} {game["game_id"][:10]} · '
                      f'{mins_until:5.1f}min pre → freeze')
    print(f'  {sport}: {frozen}/{considered} in-window freezes')
    return (frozen, considered)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport',
                   choices=list(SPORT_TABLE.keys()) + ['ALL'], default='ALL')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    sports = list(SPORT_TABLE.keys()) if args.sport == 'ALL' else [args.sport]
    print(f'=== freeze_closing_lines · {"/".join(sports)} '
          f'{"[DRY]" if args.dry_run else ""} ===')
    tf = tc = 0
    for s in sports:
        f, c = run_sport(s, dry_run=args.dry_run); tf += f; tc += c
    print(f'\n  ✓ {tf}/{tc} closes frozen')


if __name__ == '__main__':
    main()
