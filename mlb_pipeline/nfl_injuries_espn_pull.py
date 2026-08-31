"""NFL injury report pull from ESPN (preseason/Week 1 source).

2026-08-31: nflverse `import_injuries()` doesn't publish season-N parquet
until games start (Week 1+). Pre-Week-1 the nflverse-based puller
returned 0 rows and left `nfl_injuries` empty — game cards had no
injury count during the critical pre-Week-1 window.

ESPN's public `/injuries` endpoint returns live per-team injury reports
year-round (32 team blocks, no auth required). Same UNIQUE index
(season, week, team, player_name) so this coexists with the nflverse
puller — nflverse takes over once season-N parquet drops.

Usage:
    python nfl_injuries_espn_pull.py                    # all 32 teams, current week
    python nfl_injuries_espn_pull.py --dry-run          # print, no write
    python nfl_injuries_espn_pull.py --team KC          # single team test
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
H_READ  = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

ESPN_URL = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries'

# ESPN team displayName → nflverse abbreviation.
# Same mapping ncaaf_odds_pull uses via nfl_team_aliases would be ideal;
# for now hardcoded 32-team map (rare to change).
ESPN_TEAM_ABBR = {
    'Arizona Cardinals': 'ARI', 'Atlanta Falcons': 'ATL', 'Baltimore Ravens': 'BAL',
    'Buffalo Bills': 'BUF', 'Carolina Panthers': 'CAR', 'Chicago Bears': 'CHI',
    'Cincinnati Bengals': 'CIN', 'Cleveland Browns': 'CLE', 'Dallas Cowboys': 'DAL',
    'Denver Broncos': 'DEN', 'Detroit Lions': 'DET', 'Green Bay Packers': 'GB',
    'Houston Texans': 'HOU', 'Indianapolis Colts': 'IND', 'Jacksonville Jaguars': 'JAX',
    'Kansas City Chiefs': 'KC', 'Las Vegas Raiders': 'LV', 'Los Angeles Chargers': 'LAC',
    'Los Angeles Rams': 'LA', 'Miami Dolphins': 'MIA', 'Minnesota Vikings': 'MIN',
    'New England Patriots': 'NE', 'New Orleans Saints': 'NO', 'New York Giants': 'NYG',
    'New York Jets': 'NYJ', 'Philadelphia Eagles': 'PHI', 'Pittsburgh Steelers': 'PIT',
    'San Francisco 49ers': 'SF', 'Seattle Seahawks': 'SEA', 'Tampa Bay Buccaneers': 'TB',
    'Tennessee Titans': 'TEN', 'Washington Commanders': 'WAS',
}

# ESPN status labels → nfl_injuries.injury_status canonical values
# ('Out' | 'Doubtful' | 'Questionable' | 'Full')
STATUS_MAP = {
    'out':               'Out',
    'doubtful':          'Doubtful',
    'questionable':      'Questionable',
    'probable':          'Questionable',  # merged into Q per 2019 rule change
    'day-to-day':        'Questionable',
    'injured reserve':   'Out',
    'ir':                'Out',
    'physically unable': 'Out',
    'pup':               'Out',
    'suspended':         'Out',
    'nfi':               'Out',
    'active':            'Full',
    'full':              'Full',
    'limited':           'Questionable',
    'dnp':               'Out',
    'did not participate': 'Out',
}


def _map_status(raw: Optional[str]) -> Optional[str]:
    if not raw: return None
    s = raw.strip().lower()
    if s in STATUS_MAP: return STATUS_MAP[s]
    # Substring match for unfamiliar wordings
    for k, v in STATUS_MAP.items():
        if k in s: return v
    return None


def _current_nfl_week(today: date) -> tuple[int, int]:
    """Return (season, week). Season = year the season started (Aug-Jul).
    Week 1 = first Thursday of September; anything before = Week 0
    (preseason bucket per nfl_injuries schema convention)."""
    year = today.year if today.month >= 2 else today.year - 1
    sept1 = date(year, 9, 1)
    first_thu_offset = (3 - sept1.weekday()) % 7
    wk1_start = date(year, 9, 1 + first_thu_offset)
    if today < wk1_start:
        return year, 0  # preseason bucket
    return year, min(18, (today - wk1_start).days // 7 + 1)


def fetch_espn_injuries() -> list:
    r = requests.get(ESPN_URL, timeout=15)
    if r.status_code != 200:
        print(f'  ⚠ ESPN injuries: {r.status_code} {r.text[:200]}')
        return []
    return r.json().get('injuries', []) or []


def parse_team_block(team_block: dict, season: int, week: int,
                     today_iso: str) -> list[dict]:
    """Convert ESPN team block to list of nfl_injuries rows."""
    team_name = team_block.get('displayName') or ''
    abbr = ESPN_TEAM_ABBR.get(team_name)
    if not abbr:
        return []
    rows = []
    for inj in team_block.get('injuries', []) or []:
        athl = inj.get('athlete') or {}
        name = athl.get('displayName')
        if not name: continue
        status = _map_status(inj.get('status'))
        if not status: continue
        details = inj.get('details') or {}
        body_part = details.get('type')          # 'Ankle', 'Knee', etc.
        detail_text = details.get('detail')       # 'ACL', 'Not Specified', etc.
        if body_part and detail_text and detail_text.lower() != 'not specified':
            body_part = f'{body_part} ({detail_text})'
        rows.append({
            'season': season,
            'week': week,
            'team': abbr,
            'player_name': name,
            'player_id': str(athl.get('id')) if athl.get('id') else None,
            'position': (athl.get('position') or {}).get('abbreviation'),
            'injury_status': status,
            'practice_status': None,   # ESPN doesn't publish practice status
            'body_part': body_part,
            'report_date': today_iso,
        })
    return rows


def upsert_injuries(rows: list[dict], dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows[:15]:
            print(f'  [DRY] {r["team"]:3s} · {r["player_name"]:22s} · '
                  f'{r["injury_status"]:12s} · {r.get("body_part") or "-"}')
        if len(rows) > 15:
            print(f'  [DRY] ... {len(rows) - 15} more')
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/nfl_injuries?on_conflict=season,week,team,player_name',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(team_filter: Optional[str] = None, dry_run: bool = False) -> None:
    today = datetime.now(timezone.utc).date()
    season, week = _current_nfl_week(today)
    print(f'== NFL injuries · ESPN · season {season} · week {week}'
          f'{" [DRY]" if dry_run else ""} ==')

    blocks = fetch_espn_injuries()
    if not blocks:
        print('  ✗ no data from ESPN endpoint')
        return
    print(f'  ESPN returned {len(blocks)} team blocks')

    all_rows = []
    today_iso = today.isoformat()
    for tb in blocks:
        if team_filter and ESPN_TEAM_ABBR.get(tb.get('displayName') or '') != team_filter:
            continue
        all_rows.extend(parse_team_block(tb, season, week, today_iso))

    if not all_rows:
        print('  no injury rows parsed')
        return

    n = upsert_injuries(all_rows, dry_run=dry_run)
    from collections import Counter
    c = Counter(r['injury_status'] for r in all_rows)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}persisted {n} injury rows · by status: {dict(c)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--team', help='Filter to single team abbrev (KC, NE, ...)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(team_filter=args.team, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
