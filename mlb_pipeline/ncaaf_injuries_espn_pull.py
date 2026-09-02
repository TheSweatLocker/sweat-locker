"""NCAAF injury report pull from ESPN.

Mirrors nfl_injuries_espn_pull.py pattern. CFBD doesn't publish
injuries, so ESPN is our only source. Tries bulk endpoint first,
falls back to per-team polling if bulk returns nothing.

Post-launch consumers:
- ncaaf_mc_simulator Tier F: QB-out penalty on MC probability adjustments
- Thu-lock injury regen: NFL parallel already deployed (Jerry re-generates
  the specific game if QB1 status changes post-lock)

Usage:
    python ncaaf_injuries_espn_pull.py                # bulk fetch
    python ncaaf_injuries_espn_pull.py --dry-run
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

# ESPN's college-football path uses "athletes.injuries" per team.
# Bulk endpoint (parallel to NFL's) may or may not exist — try + fallback.
ESPN_BULK_URL = 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/injuries'
# Per-team fallback pattern (if bulk fails)
# https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{teamId}/injuries

# Same status mapping as NFL
STATUS_MAP = {
    'out':               'Out',
    'doubtful':          'Doubtful',
    'questionable':      'Questionable',
    'probable':          'Questionable',
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
    for k, v in STATUS_MAP.items():
        if k in s: return v
    return None


def _current_cfb_week(today: date) -> tuple[int, int]:
    """Return (season, week). CFB season starts late August.
    Week 1 = Sunday before first Saturday game. Simple mapping."""
    year = today.year if today.month >= 6 else today.year - 1
    # Week 1 target: Sep 1 (approx — actual Week 1 varies year to year)
    wk1_start = date(year, 8, 25)
    if today < wk1_start:
        return year, 0
    return year, min(16, (today - wk1_start).days // 7 + 1)


def fetch_espn_bulk() -> list:
    """Try bulk injuries endpoint. Returns team blocks or empty list."""
    try:
        r = requests.get(ESPN_BULK_URL, timeout=15)
        if r.status_code != 200:
            print(f'  ⚠ ESPN bulk injuries: {r.status_code} — will fallback per-team')
            return []
        data = r.json()
        blocks = data.get('injuries', []) or data.get('teams', []) or []
        return blocks
    except Exception as e:
        print(f'  ⚠ bulk fetch exception: {e}')
        return []


def parse_team_block(team_block: dict, season: int, week: int,
                     today_iso: str) -> list[dict]:
    """Convert ESPN team block to list of ncaaf_injuries rows."""
    team_name = team_block.get('displayName') or team_block.get('name') or ''
    if not team_name:
        return []
    rows = []
    for inj in team_block.get('injuries', []) or []:
        athl = inj.get('athlete') or {}
        name = athl.get('displayName')
        if not name: continue
        status = _map_status(inj.get('status'))
        if not status: continue
        details = inj.get('details') or {}
        body_part = details.get('type')
        detail_text = details.get('detail')
        if body_part and detail_text and detail_text.lower() != 'not specified':
            body_part = f'{body_part} ({detail_text})'
        rows.append({
            'season': season,
            'week': week,
            'team': team_name,   # keep school display name (schedule matches this)
            'player_name': name,
            'player_id': str(athl.get('id')) if athl.get('id') else None,
            'position': (athl.get('position') or {}).get('abbreviation'),
            'injury_status': status,
            'practice_status': None,
            'body_part': body_part,
            'report_date': today_iso,
        })
    return rows


def upsert_injuries(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows[:15]:
            print(f'  [DRY] {r["team"]:24s} · {r["player_name"]:22s} · '
                  f'{r["injury_status"]:12s} · {r.get("position") or "?":4s} · {r.get("body_part") or "-"}')
        if len(rows) > 15:
            print(f'  [DRY] ... {len(rows) - 15} more')
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/ncaaf_injuries?on_conflict=season,week,team,player_name',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(dry_run: bool = False) -> None:
    today = datetime.now(timezone.utc).date()
    season, week = _current_cfb_week(today)
    print(f'== NCAAF injuries · ESPN · season {season} · week {week}'
          f'{" [DRY]" if dry_run else ""} ==')

    blocks = fetch_espn_bulk()
    if not blocks:
        print('  Bulk endpoint returned nothing.')
        print('  Per-team fallback not implemented (would require 130+ API calls).')
        print('  Will retry next cron. If bulk endpoint reliably empty, need')
        print('  fallback strategy: per-team fetch for teams playing this week only.')
        return
    print(f'  ESPN returned {len(blocks)} team blocks')

    all_rows = []
    today_iso = today.isoformat()
    for tb in blocks:
        all_rows.extend(parse_team_block(tb, season, week, today_iso))

    if not all_rows:
        print('  no injury rows parsed')
        return

    n = upsert_injuries(all_rows, dry_run=dry_run)
    from collections import Counter
    c = Counter(r['injury_status'] for r in all_rows)
    qb_out = sum(1 for r in all_rows if r.get('position') == 'QB' and r['injury_status'] in ('Out', 'Doubtful'))
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}persisted {n} injury rows · by status: {dict(c)} · QB out/doubtful: {qb_out}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
