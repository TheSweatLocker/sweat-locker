"""NCAAF AP poll rankings pull from CFBD.

Fetches current week's AP top-25 + patches home_ap_rank / away_ap_rank
onto every ncaaf_game_context row for upcoming games. Powers the
⭐ Ranked matchup badge on game cards.

CFBD endpoint: /rankings?year=X&week=Y&seasonType=regular
Returns list of poll rows; we pick the AP poll specifically.

Usage:
    python ncaaf_rankings_pull.py                  # current week
    python ncaaf_rankings_pull.py --week 3
    python ncaaf_rankings_pull.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta, date
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


def _current_cfb_week() -> tuple[int, int]:
    """Return (season, week). Week 1 typically starts late August."""
    today = datetime.now(timezone.utc).date()
    year = today.year if today.month >= 6 else today.year - 1
    wk1_start = date(year, 8, 25)
    if today < wk1_start:
        return year, 1  # pre-season → target week 1 rankings
    return year, min(16, (today - wk1_start).days // 7 + 1)


def fetch_ap_rankings(season: int, week: int) -> dict:
    """Return {team_school: rank_int}. Empty dict on failure."""
    if not CFBD_KEY:
        print('  ⚠ CFBD_API_KEY missing')
        return {}
    r = requests.get(
        f'{CFBD_BASE}/rankings',
        headers={'Authorization': f'Bearer {CFBD_KEY}'},
        params={'year': season, 'week': week, 'seasonType': 'regular'},
        timeout=20,
    )
    if r.status_code != 200:
        print(f'  ⚠ CFBD rankings {r.status_code}: {r.text[:120]}')
        return {}
    data = r.json()
    if not data:
        print(f'  ⚠ CFBD returned empty rankings for week {week}')
        return {}

    # data[0] = week snapshot; polls[] contains multiple polls
    polls = (data[0] or {}).get('polls', []) or []
    ap_poll = next((p for p in polls if (p.get('poll') or '').lower() == 'ap top 25'), None)
    if not ap_poll:
        print(f'  ⚠ AP poll not found for week {week} (available: {[p.get("poll") for p in polls]})')
        return {}

    ranks = {}
    for entry in (ap_poll.get('ranks') or []):
        school = entry.get('school')
        rank = entry.get('rank')
        if school and isinstance(rank, int):
            ranks[school] = rank
    return ranks


def fetch_upcoming_games() -> list:
    """Get NCAAF games in next 8 days needing rank patch."""
    today_iso = datetime.now(timezone.utc).date().isoformat()
    horizon_iso = (datetime.now(timezone.utc).date() + timedelta(days=8)).isoformat()
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_game_context',
        headers=H_READ,
        params={
            'select': 'game_id,home_team,away_team,game_date,home_ap_rank,away_ap_rank',
            'game_date': f'gte.{today_iso}',
            'game_date': f'lte.{horizon_iso}',  # last one wins in PostgREST
            'order': 'game_date.asc',
            'limit': '400',
        },
        timeout=30,
    )
    if r.status_code != 200:
        print(f'  ⚠ ctx fetch {r.status_code}')
        return []
    return r.json()


def patch_game(game_id: str, home_rank: Optional[int], away_rank: Optional[int],
               dry_run: bool = False) -> bool:
    patch = {'home_ap_rank': home_rank, 'away_ap_rank': away_rank}
    if dry_run:
        print(f'  [DRY] {game_id}: home={home_rank} · away={away_rank}')
        return True
    r = requests.patch(
        f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{game_id}',
        headers=H_WRITE, json=patch, timeout=15,
    )
    return r.status_code in (200, 204)


def run(target_week: Optional[int] = None, dry_run: bool = False) -> None:
    season, cur_week = _current_cfb_week()
    week = target_week or cur_week
    print(f'== NCAAF AP rankings · season {season} · week {week}'
          f'{" [DRY]" if dry_run else ""} ==')

    ranks = fetch_ap_rankings(season, week)
    if not ranks:
        print('  no rankings, aborting')
        return
    print(f'  AP top-25 pulled: {len(ranks)} teams')

    games = fetch_upcoming_games()
    print(f'  upcoming ncaaf games: {len(games)}')
    if not games: return

    patched = 0
    ranked_matchups = 0
    for g in games:
        home = g.get('home_team')
        away = g.get('away_team')
        h_rank = ranks.get(home) if home else None
        a_rank = ranks.get(away) if away else None
        if h_rank and a_rank: ranked_matchups += 1
        if patch_game(g['game_id'], h_rank, a_rank, dry_run):
            patched += 1

    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}patched {patched}/{len(games)} games · {ranked_matchups} both-ranked matchups')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--week', type=int, help='Override CFB week (default: current)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(target_week=args.week, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
