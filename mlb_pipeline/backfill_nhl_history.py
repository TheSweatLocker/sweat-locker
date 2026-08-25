"""Backfill NHL historical games into nhl_game_results (2026-08-17).

Iterates date range calling nhl_data_client.get_scoreboard(). For each
finalized game, upserts to nhl_game_results with computed home_win +
went_to_ot flag. Skips games missing close_puckline/close_total — those
would be null for spread_result/total_result but home_win still valid.

Purpose: gives the tendencies backfill + team_form signals real
historical data to fire against BEFORE Oct 7 season opener. Also enables
backtest of playbook signals against known outcomes.

Skips already-resolved game_ids via UPSERT on conflict.

CLI:
  python backfill_nhl_history.py --start 2024-10-04 --end 2025-04-17
  python backfill_nhl_history.py --season 2024-25          # convenience
  python backfill_nhl_history.py --dry-run
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

sys.path.insert(0, str(Path(__file__).parent))
from nhl_data_client import get_scoreboard


SEASON_RANGES = {
    '2024-25': ('2024-10-04', '2025-04-17'),
    '2023-24': ('2023-10-10', '2024-04-18'),
    '2022-23': ('2022-10-07', '2023-04-14'),
}


def backfill(start: str, end: str, dry_run: bool = False) -> int:
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    total_games = 0
    written = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    d = start_d
    while d <= end_d:
        games = get_scoreboard(d.isoformat())
        if games:
            payloads = []
            for g in games:
                hs = g.get('home_score'); as_ = g.get('away_score')
                if hs is None or as_ is None: continue
                payloads.append({
                    'game_id':     g['game_id'],
                    'game_date':   d.isoformat(),
                    'home_team':   g.get('home_team'),
                    'away_team':   g.get('away_team'),
                    'home_score':  hs,
                    'away_score':  as_,
                    'total_goals': hs + as_,
                    'home_win':    hs > as_,
                    'went_to_ot':  g.get('went_to_ot', False),
                    'went_to_so':  g.get('went_to_so', False),
                    'resolved_at': now_iso,
                })
            if payloads:
                total_games += len(payloads)
                if not dry_run:
                    for i in range(0, len(payloads), 50):
                        chunk = payloads[i:i+50]
                        pr = requests.post(f'{SB}/rest/v1/nhl_game_results?on_conflict=game_id',
                                           headers=H_WRITE, json=chunk, timeout=30)
                        if pr.status_code in (200, 201, 204): written += len(chunk)
                        else: print(f'    ✗ upsert {d}: {pr.status_code} {pr.text[:150]}')
                else:
                    written += len(payloads)
                print(f'  {d}: {len(payloads)} games')
        d += timedelta(days=1)

    print(f'\n  {"[DRY] " if dry_run else ""}backfilled {written}/{total_games} games')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--start', help='YYYY-MM-DD')
    p.add_argument('--end', help='YYYY-MM-DD')
    p.add_argument('--season', help='Season label (2024-25, 2023-24, 2022-23)')
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
    print(f'=== backfill NHL history · {start} → {end} ===')
    backfill(start, end, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
