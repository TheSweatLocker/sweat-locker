"""Backfill NBA historical games into nba_game_results (2026-08-17).

Same pattern as backfill_nhl_history.py. Iterates date range calling
ESPN scoreboard for each day, upserts finalized games into
nba_game_results. Enables Elo training + tendencies backfill + signal
backtest before Oct 22 opener.

CLI:
  python backfill_nba_history.py --season 2024-25         # convenience
  python backfill_nba_history.py --start 2024-10-22 --end 2025-06-22
  python backfill_nba_history.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, time
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

sys.path.insert(0, str(Path(__file__).parent))
from nba_data_client import get_scoreboard


# NBA regular season + playoffs windows
SEASON_RANGES = {
    '2024-25': ('2024-10-22', '2025-06-22'),
    '2023-24': ('2023-10-24', '2024-06-17'),
    '2022-23': ('2022-10-18', '2023-06-12'),
}


def _season_str_from_date(d: date) -> str:
    if d.month >= 8: return f'{d.year}-{str(d.year+1)[-2:]}'
    return f'{d.year-1}-{str(d.year)[-2:]}'


def backfill(start: str, end: str, dry_run: bool = False) -> int:
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    total = 0; written = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    d = start_d
    while d <= end_d:
        games = get_scoreboard(d.isoformat())
        if games:
            season = _season_str_from_date(d)
            payloads = []
            for g in games:
                hs = g.get('home_score'); as_ = g.get('away_score')
                if hs is None or as_ is None: continue
                payloads.append({
                    'game_id':     g['game_id'],
                    'game_date':   d.isoformat(),
                    'season':      season,
                    'home_team':   g.get('home_team'),
                    'away_team':   g.get('away_team'),
                    'home_score':  hs,
                    'away_score':  as_,
                    'total_points': hs + as_,
                    'home_win':    hs > as_,
                })
                # NOTE: home_abbrev / away_abbrev / went_to_ot / resolved_at
                # dropped from payload until 20260817_nba_foundation.sql
                # migration lands. Those fields will backfill NULL on
                # these rows but the ML tendency signals only need
                # home_win which is populated.
            if payloads:
                total += len(payloads)
                if not dry_run:
                    for i in range(0, len(payloads), 50):
                        chunk = payloads[i:i+50]
                        pr = requests.post(f'{SB}/rest/v1/nba_game_results?on_conflict=game_id',
                                           headers=H_WRITE, json=chunk, timeout=30)
                        if pr.status_code in (200,201,204): written += len(chunk)
                        else: print(f'    ✗ upsert {d}: {pr.status_code} {pr.text[:120]}')
                else:
                    written += len(payloads)
                if len(payloads) >= 5: print(f'  {d}: {len(payloads)} games')
        d += timedelta(days=1)
        # Rate limit ESPN — half a second between date fetches. Backfill
        # ~200 days takes ~2 min instead of hammering the API.
        time.sleep(0.5)

    print(f'\n  {"[DRY] " if dry_run else ""}backfilled {written}/{total} games')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--start'); p.add_argument('--end'); p.add_argument('--season')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    if args.season:
        if args.season not in SEASON_RANGES:
            raise SystemExit(f'Unknown season. Options: {list(SEASON_RANGES.keys())}')
        start, end = SEASON_RANGES[args.season]
    else:
        start, end = args.start, args.end
    if not start or not end:
        raise SystemExit('Need --start + --end OR --season')
    print(f'=== backfill NBA history · {start} → {end} ===')
    backfill(start, end, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
