"""NCAAF recruiting composite pull from CFBD /talent (2026-09-02).

Rolling 4-year composite recruiting rank. Powers "recruiting mismatch"
signals: team dramatically overperforming/underperforming their talent
tier (regression candidate).

Blue-chip programs: 900+ composite
Mid-major: 600-800
G5: 400-600
FCS: <400

Usage:
    python ncaaf_recruiting_pull.py                # current season
    python ncaaf_recruiting_pull.py --season 2026
    python ncaaf_recruiting_pull.py --dry-run
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


def fetch_talent(season: int) -> dict:
    if not CFBD_KEY: return {}
    r = requests.get(
        f'{CFBD_BASE}/talent',
        headers={'Authorization': f'Bearer {CFBD_KEY}'},
        params={'year': season}, timeout=20,
    )
    if r.status_code != 200:
        print(f'  ⚠ CFBD talent {r.status_code}: {r.text[:120]}')
        return {}
    out = {}
    for row in r.json() or []:
        team = row.get('school') or row.get('team')
        talent = row.get('talent')
        if team and talent is not None:
            try: out[team] = float(talent)
            except (TypeError, ValueError): pass
    return out


def run(target_season: Optional[int] = None, dry_run: bool = False):
    season = target_season or _current_cfb_season()
    print(f'== NCAAF talent pull · season {season}{" [DRY]" if dry_run else ""} ==')

    talent = fetch_talent(season)
    if not talent:
        print('  no talent data (CFBD /talent may not publish current year until Signing Day)')
        # Try prior season as fallback
        talent = fetch_talent(season - 1)
        if talent:
            print(f'  → falling back to season {season - 1} ({len(talent)} teams)')
        else:
            return
    print(f'  talent composite for {len(talent)} teams')

    # Upsert team_stats
    if not dry_run:
        for team, val in talent.items():
            payload = {'team': team, 'season': season, 'talent_composite': round(val, 1)}
            requests.post(
                f'{SB}/rest/v1/ncaaf_team_stats?on_conflict=team,season',
                headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates'},
                json=payload, timeout=15,
            )

    # Patch upcoming games' home_talent / away_talent
    today_iso = datetime.now(timezone.utc).date().isoformat()
    games = requests.get(
        f'{SB}/rest/v1/ncaaf_game_context',
        headers=H_READ,
        params={'select': 'game_id,home_team,away_team',
                'game_date': f'gte.{today_iso}', 'limit': '400'},
        timeout=20,
    ).json()

    patched = 0
    for g in (games or []):
        h_t = talent.get(g.get('home_team'))
        a_t = talent.get(g.get('away_team'))
        if h_t is None and a_t is None: continue
        patch = {'home_talent': round(h_t, 1) if h_t is not None else None,
                 'away_talent': round(a_t, 1) if a_t is not None else None}
        if dry_run:
            print(f'  [DRY] {g["game_id"]}: {patch}'); patched += 1; continue
        pr = requests.patch(
            f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{g["game_id"]}',
            headers=H_WRITE, json=patch, timeout=15,
        )
        if pr.status_code in (200, 204): patched += 1

    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}patched {patched} games')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(target_season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
