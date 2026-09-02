"""NFL Jerry Thu-lock injury-triggered regen (2026-09-02).

Runs periodically Thu-Sun during game week. Detects QB1 status changes
post-Thu-lock and triggers targeted regen of affected game reads.

Logic:
  1. Query current nfl_injuries for QBs with status Out/Doubtful
  2. Cross-reference with nfl_starters QB1 for current week
  3. Find each affected team's upcoming game (nfl_game_context)
  4. For each game with QB1 out AND existing Thu-lock (jerry_cache row):
     - Compare jerry_cache _locked_at timestamp to injury report_date
     - If injury newer than lock → fire generate_nfl_game_reads.py
       --force --game-id X to regenerate
  5. Log all regen attempts to console (also to a regen_log table if wanted)

Idempotent — running twice on same game is fine (LLM call costs $ but
doesn't corrupt data). Silent no-op if no changes.

USAGE:
    python nfl_injury_regen_check.py                 # normal run
    python nfl_injury_regen_check.py --dry-run       # print, don't regen
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta, date
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
H_READ  = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _current_nfl_week() -> tuple[int, int]:
    today = datetime.now(timezone.utc).date()
    year = today.year if today.month >= 2 else today.year - 1
    sept1 = date(year, 9, 1)
    first_thu_offset = (3 - sept1.weekday()) % 7
    wk1_start = date(year, 9, 1 + first_thu_offset)
    if today < wk1_start: return year, 0
    return year, min(18, (today - wk1_start).days // 7 + 1)


def fetch_qb_out_list(season: int, week: int) -> list[dict]:
    """Return list of {team, player_name, status, report_date} for QBs
    with Out/Doubtful status this week."""
    r = requests.get(
        f'{SB}/rest/v1/nfl_injuries',
        headers=H_READ,
        params={
            'season': f'eq.{season}', 'week': f'eq.{week}',
            'position': 'eq.QB',
            'injury_status': 'in.(Out,Doubtful)',
            'select': 'team,player_name,injury_status,report_date',
        },
        timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def fetch_qb1_starters(season: int, week: int) -> dict:
    """Return {team_abbrev: qb1_name} from nfl_starters."""
    r = requests.get(
        f'{SB}/rest/v1/nfl_starters',
        headers=H_READ,
        params={
            'season': f'eq.{season}', 'week': f'eq.{week}',
            'position': 'eq.QB',
            'is_starter': 'eq.true',
            'select': 'team,player_name',
        },
        timeout=15,
    )
    if r.status_code != 200: return {}
    return {row['team']: row['player_name'] for row in r.json() if row.get('team') and row.get('player_name')}


def fetch_upcoming_nfl_games() -> list:
    """Games with kickoff in next 8 days that have a Thu-lock in jerry_cache."""
    today_iso = datetime.now(timezone.utc).date().isoformat()
    hi_iso = (datetime.now(timezone.utc).date() + timedelta(days=8)).isoformat()
    r = requests.get(
        f'{SB}/rest/v1/nfl_game_context',
        headers=H_READ,
        params={
            'select': 'game_id,home_team,away_team,game_date,kickoff_utc',
            'game_date': f'gte.{today_iso}',
            'order': 'kickoff_utc.asc',
            'limit': '80',
        },
        timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def fetch_lock_metadata(game_id: str) -> Optional[dict]:
    """Return {'locked_at': iso, 'data': dict} for the jerry_cache row keyed
    on this game's current week key, or None if no lock exists."""
    # We look up by game_id + sport (unique constraint), then inspect payload
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_READ,
        params={
            'sport': 'eq.NFL',
            'select': 'cache_key,data,fetched_at',
            'cache_key': f'like.game_read_{game_id}_nfl_week_%',
            'order': 'fetched_at.desc',
            'limit': '1',
        },
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    if not rows: return None
    row = rows[0]
    try:
        data = json.loads(row['data']) if isinstance(row['data'], str) else (row['data'] or {})
    except Exception:
        data = {}
    return {
        'cache_key': row.get('cache_key'),
        'locked_at': data.get('_locked_at') or row.get('fetched_at'),
        'data': data,
    }


def trigger_regen(game_id: str, dry_run: bool = False) -> bool:
    """Fire generate_nfl_game_reads.py --force --game-id X."""
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), 'generate_nfl_game_reads.py'),
           '--force', '--game-id', game_id]
    if dry_run:
        print(f'    [DRY] would run: {" ".join(cmd)}')
        return True
    try:
        subprocess.run(cmd, check=False, timeout=180)
        return True
    except Exception as e:
        print(f'    ⚠ regen failed: {e}')
        return False


def run(dry_run: bool = False):
    season, week = _current_nfl_week()
    print(f'== NFL injury regen check · season {season} · week {week}'
          f'{" [DRY]" if dry_run else ""} ==')
    if week == 0:
        print('  preseason bucket — skip'); return

    qb_out = fetch_qb_out_list(season, week)
    if not qb_out:
        print('  no QBs Out/Doubtful this week'); return
    print(f'  QBs Out/Doubtful: {len(qb_out)}')

    qb1_starters = fetch_qb1_starters(season, week)
    print(f'  QB1 starters mapped: {len(qb1_starters)}')

    # Which teams have their listed QB1 in the Out list?
    affected_teams = set()
    for inj in qb_out:
        team = inj.get('team')
        player = inj.get('player_name')
        qb1 = qb1_starters.get(team)
        if qb1 and qb1 == player:
            affected_teams.add(team)
            print(f'  🚨 {team} QB1 {player}: {inj.get("injury_status")}')

    if not affected_teams:
        print('  no QB1 status changes'); return

    # Find games involving affected teams with existing Thu-lock
    games = fetch_upcoming_nfl_games()
    regens = 0
    for g in games:
        home, away = g.get('home_team'), g.get('away_team')
        if not (home in affected_teams or away in affected_teams): continue
        gid = g.get('game_id')
        if not gid: continue
        lock = fetch_lock_metadata(gid)
        if not lock:
            print(f'  {away} @ {home}: no lock yet, skip (Thu run will pick up)')
            continue
        # Regen this game (unconditional — injury status changed since lock)
        print(f'  🔄 regenerating: {away} @ {home}  (lock at {lock.get("locked_at","?")[:19]})')
        if trigger_regen(gid, dry_run):
            regens += 1

    print(f'\n  {"[DRY] would regen" if dry_run else "triggered"}: {regens} games')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
