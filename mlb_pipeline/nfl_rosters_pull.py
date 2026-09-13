"""Pull NFL active rosters from nflverse and upsert into nfl_rosters_current.

Andy 9/13: "Cousins plays in vegas what are you talking about" —
generate_nfl_game_reads key-players aggregator was surfacing offseason
ghosts because nfl_player_stats lags trades until the traded player
logs a game for their new team. This script closes that gap by
pulling the authoritative nflverse roster CSV weekly and upserting
into nfl_rosters_current.

Data: github.com/nflverse/nflverse-data/releases/download/rosters/
      roster_{season}.csv
Refreshed by nflverse when players sign / get traded / released.
2026 season: 2963 rows across 32 teams.

Cron: Tuesday morning before Wed NFL read regen (Thu-lock cycle).

Run:
  python nfl_rosters_pull.py                 # current season, all teams
  python nfl_rosters_pull.py --season 2026
  python nfl_rosters_pull.py --dry-run       # log only, no DB writes
"""
import argparse
import csv
import io
import os
import sys
import urllib.request
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

for _p in ('mlb_pipeline/.env', '.env'):
    if os.path.exists(_p):
        load_dotenv(_p)
        break

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
                or os.environ.get('SUPABASE_KEY'))
if not (SUPABASE_URL and SUPABASE_KEY):
    sys.exit('SUPABASE_URL / SUPABASE_KEY not set')

H_WRITE = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=merge-duplicates,return=minimal',
}

NFLVERSE_URL = (
    'https://github.com/nflverse/nflverse-data/releases/download/'
    'rosters/roster_{season}.csv'
)


def _to_int(v):
    try:
        s = str(v).strip()
        return int(float(s)) if s else None
    except (TypeError, ValueError):
        return None


def fetch_roster(season: int) -> list[dict]:
    url = NFLVERSE_URL.format(season=season)
    print(f'  fetching {url}')
    with urllib.request.urlopen(url, timeout=30) as r:
        raw = r.read().decode('utf-8', errors='replace')
    reader = csv.DictReader(io.StringIO(raw))
    rows = [row for row in reader]
    print(f'  {len(rows)} roster rows loaded from nflverse')
    return rows


def upsert_batch(rows: list[dict], batch_size: int = 500) -> int:
    written = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        payload = []
        for row in chunk:
            team = (row.get('team') or '').strip()
            player = (row.get('full_name') or row.get('player_name') or '').strip()
            season = _to_int(row.get('season'))
            if not (team and player and season):
                continue
            payload.append({
                'season': season,
                'team': team,
                'player_name': player,
                'position': (row.get('position') or '').strip() or None,
                'depth_chart_position': (row.get('depth_chart_position') or '').strip() or None,
                'jersey_number': _to_int(row.get('jersey_number')),
                'status': (row.get('status') or '').strip() or None,
                'height': (row.get('height') or '').strip() or None,
                'weight': _to_int(row.get('weight')),
                'college': (row.get('college') or '').strip() or None,
                'gsis_id': (row.get('gsis_id') or '').strip() or None,
                'espn_id': (row.get('espn_id') or '').strip() or None,
                'updated_at': now_iso,
            })
        if not payload:
            continue
        url = (f'{SUPABASE_URL}/rest/v1/nfl_rosters_current'
               f'?on_conflict=season,team,player_name')
        r = requests.post(url, headers=H_WRITE, json=payload, timeout=30)
        if r.status_code in (200, 201, 204):
            written += len(payload)
        else:
            print(f'  ⚠ batch {i}: {r.status_code} {r.text[:180]}')
    return written


def run(season: int | None = None, dry_run: bool = False) -> None:
    if season is None:
        now = datetime.now(timezone.utc)
        # NFL season crosses year boundary; June onward = new season
        season = now.year if now.month >= 6 else now.year - 1
    print(f'=== nfl_rosters_pull · season={season} ({"DRY" if dry_run else "APPLY"}) ===')
    rows = fetch_roster(season)
    if dry_run:
        print(f'  would upsert {len(rows)} rows — dry-run, skipping DB write')
        # Sample: Cousins verification
        cousins = [r for r in rows if (r.get('full_name') or '').strip() == 'Kirk Cousins']
        for c in cousins:
            print(f'    Cousins → team={c.get("team")} pos={c.get("position")} '
                  f'depth={c.get("depth_chart_position")} status={c.get("status")}')
        return
    written = upsert_batch(rows)
    print(f'\n✅ nfl_rosters_current: wrote/merged {written} rows for season {season}')


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--season', type=int)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
