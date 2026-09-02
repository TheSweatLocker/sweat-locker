"""NCAAF Strength of Record pull from CFBD.

Fetches per-team SoR rating for the current season + patches
home_sor / away_sor onto ncaaf_game_context rows for upcoming games.

CFBD endpoint: /ratings/sor?year=X (returns per-team SoR points).
Refresh cadence: weekly (Tuesday when polls update).

Post-launch signal candidates using this data:
- SoR gap > 3 points but market spread doesn't reflect it → BACK the SoR-favored team
- Both teams high SoR → higher-quality matchup (info signal)

Usage:
    python ncaaf_sor_pull.py                # current season
    python ncaaf_sor_pull.py --season 2026
    python ncaaf_sor_pull.py --dry-run
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
CFBD_KEY = os.environ.get('CFBD_API_KEY')
H_READ  = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}
CFBD_BASE = 'https://api.collegefootballdata.com'

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _current_cfb_season() -> int:
    today = datetime.now(timezone.utc).date()
    return today.year if today.month >= 6 else today.year - 1


def fetch_sor(season: int) -> dict:
    """Return {team_school: sor_rating_float}. Empty dict on failure."""
    if not CFBD_KEY:
        print('  ⚠ CFBD_API_KEY missing')
        return {}
    r = requests.get(
        f'{CFBD_BASE}/ratings/sor',
        headers={'Authorization': f'Bearer {CFBD_KEY}'},
        params={'year': season},
        timeout=20,
    )
    if r.status_code != 200:
        print(f'  ⚠ CFBD SoR {r.status_code}: {r.text[:120]}')
        return {}
    out = {}
    for row in r.json() or []:
        team = row.get('team')
        rating = row.get('rating')
        if team and rating is not None:
            try:
                out[team] = float(rating)
            except (TypeError, ValueError):
                pass
    return out


def fetch_upcoming_games() -> list:
    today_iso = datetime.now(timezone.utc).date().isoformat()
    hi_iso = (datetime.now(timezone.utc).date() + timedelta(days=8)).isoformat()
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
    if r.status_code != 200:
        print(f'  ⚠ ctx fetch {r.status_code}')
        return []
    return r.json()


def patch_game(game_id: str, home_sor: Optional[float], away_sor: Optional[float],
               dry_run: bool = False) -> bool:
    patch = {'home_sor': home_sor, 'away_sor': away_sor}
    if dry_run:
        print(f'  [DRY] {game_id}: home={home_sor} · away={away_sor}')
        return True
    r = requests.patch(
        f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{game_id}',
        headers=H_WRITE, json=patch, timeout=15,
    )
    return r.status_code in (200, 204)


def run(target_season: Optional[int] = None, dry_run: bool = False) -> None:
    season = target_season or _current_cfb_season()
    print(f'== NCAAF SoR pull · season {season}'
          f'{" [DRY]" if dry_run else ""} ==')

    ratings = fetch_sor(season)
    if not ratings:
        print('  no SoR data (may be too early in season — CFBD publishes after Week 3-4)')
        return
    print(f'  SoR ratings pulled: {len(ratings)} teams')

    games = fetch_upcoming_games()
    print(f'  upcoming ncaaf games: {len(games)}')
    if not games: return

    patched = 0
    both_populated = 0
    for g in games:
        home = g.get('home_team')
        away = g.get('away_team')
        h_sor = ratings.get(home) if home else None
        a_sor = ratings.get(away) if away else None
        if h_sor is not None and a_sor is not None: both_populated += 1
        if patch_game(g['game_id'], h_sor, a_sor, dry_run):
            patched += 1

    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}patched {patched}/{len(games)} games · both teams populated: {both_populated}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, help='Override season (default: current)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(target_season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
