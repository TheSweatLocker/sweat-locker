"""NCAAF turnover margin computer (2026-09-02).

Computes per-team season turnover margin from ncaaf_team_stats + patches
home_to_margin / away_to_margin onto upcoming ncaaf_game_context rows.

TO margin = (def_ints + def_fumbles_rec) - (pass_ints + fumbles_lost)
          = takeaways - giveaways

Historical: extreme TO margin regresses toward mean the following month
(~80% regression at |margin| >= 8). Signal for BACK regression → FADE
teams with hot TO streaks, BACK teams with cold TO streaks.

Post-launch consumer: Vault Match pattern fires when team has TO margin
>= +8, FADE the team (mean reversion play).

USAGE:
    python ncaaf_to_margin_pull.py                # current season
    python ncaaf_to_margin_pull.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
H_READ  = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _current_cfb_season() -> int:
    today = datetime.now(timezone.utc).date()
    return today.year if today.month >= 6 else today.year - 1


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def fetch_to_margins(season: int) -> dict:
    """Return {team: to_margin_int}."""
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_team_stats',
        headers=H_READ,
        params={
            'season': f'eq.{season}',
            'select': 'team,pass_ints,fumbles_lost,def_ints,def_fumbles_rec',
        },
        timeout=30,
    )
    out = {}
    for row in (r.json() if r.status_code == 200 else []):
        team = row.get('team')
        if not team: continue
        giveaways = (_i(row.get('pass_ints')) or 0) + (_i(row.get('fumbles_lost')) or 0)
        takeaways = (_i(row.get('def_ints')) or 0) + (_i(row.get('def_fumbles_rec')) or 0)
        out[team] = takeaways - giveaways
    return out


def fetch_upcoming_games():
    today_iso = datetime.now(timezone.utc).date().isoformat()
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_game_context',
        headers=H_READ,
        params={
            'select': 'game_id,home_team,away_team,game_date',
            'game_date': f'gte.{today_iso}',
            'order': 'game_date.asc',
            'limit': '400',
        },
        timeout=30,
    )
    return r.json() if r.status_code == 200 else []


def patch_game(game_id: str, home_to: Optional[int], away_to: Optional[int],
               dry_run: bool = False) -> bool:
    patch = {'home_to_margin': home_to, 'away_to_margin': away_to}
    if dry_run:
        print(f'  [DRY] {game_id}: home={home_to} away={away_to}')
        return True
    r = requests.patch(
        f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{game_id}',
        headers=H_WRITE, json=patch, timeout=15,
    )
    return r.status_code in (200, 204)


def run(dry_run: bool = False):
    season = _current_cfb_season()
    print(f'== NCAAF TO margin pull · season {season}'
          f'{" [DRY]" if dry_run else ""} ==')

    margins = fetch_to_margins(season)
    if not margins:
        print('  no TO margin data (ncaaf_team_stats may be empty this early)')
        return
    print(f'  TO margins computed for {len(margins)} teams')

    # Flag extremes for visibility
    hot = [(t, m) for t, m in margins.items() if m >= 6]
    cold = [(t, m) for t, m in margins.items() if m <= -6]
    print(f'  {len(hot)} teams w/ TO margin >= +6 (regression FADE candidates)')
    print(f'  {len(cold)} teams w/ TO margin <= -6 (regression BACK candidates)')

    games = fetch_upcoming_games()
    print(f'  upcoming games: {len(games)}')
    if not games: return

    patched = 0
    for g in games:
        home = g.get('home_team'); away = g.get('away_team')
        h_to = margins.get(home) if home else None
        a_to = margins.get(away) if away else None
        if patch_game(g['game_id'], h_to, a_to, dry_run):
            patched += 1

    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}patched {patched}/{len(games)} games')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
