"""Post-game prop grader (2026-08-08).

Grades every scored prop in mlb_pipeline_props by fetching actual player
stats from MLB Stats API boxscores. Updates result (W/L/P) and
final_value on each row.

Gap discovered 2026-08-08: yesterday's 53 scored props all had result=null.
Nothing was writing outcomes. grade_prop_jerry_reads.py inherits result
from mlb_pipeline_props, so with no upstream grader, downstream jerry_read
grading + bucket_roi rebuild all decay on stale data.

Prop → stat mapping (MLB):
  ks_over/under    → strikeOuts
  bb_over/under    → baseOnBalls
  er_over/under    → earnedRuns
  ha_over/under    → hits (pitcher)
  outs_over/under  → outs recorded from innings pitched
  hits_over        → hits (batter)

Sport-universal via SPORT_PROPS_TABLE. MLB only for now — NFL/NBA/NCAAF/
NCAAB plug in when their prop pipelines ship + we wire stat feeds.

Usage:
    python grade_props.py                    # grades yesterday
    python grade_props.py --date 2026-08-07  # specific date
    python grade_props.py --backfill 14      # backfill last 14 days
    python grade_props.py --dry-run          # print, no writes
"""
from __future__ import annotations
import argparse, json, os, sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Set by --force flag in main()
FORCE_REGRADE = False


SPORT_PROPS_TABLE = {
    'MLB': 'mlb_pipeline_props',
}

# prop_type → boxscore stat key
STAT_MAP_MLB = {
    'ks_over':   'ks',   'ks_under':   'ks',
    'bb_over':   'bb',   'bb_under':   'bb',
    'er_over':   'er',   'er_under':   'er',
    'ha_over':   'h_pit', 'ha_under':  'h_pit',
    'outs_over': 'outs', 'outs_under': 'outs',
    'hits_over': 'h_bat',
}


def yesterday_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=28)).strftime('%Y-%m-%d')


def fetch_player_stats_for_date(date_str: str) -> dict:
    """Return {player_name_lower: {ks, bb, er, h_pit, outs, h_bat}} for
    every player who appeared in an MLB game on date_str."""
    sched = json.load(urllib.request.urlopen(
        f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}', timeout=15))
    game_pks = []
    for d in sched.get('dates', []):
        for g in d.get('games', []):
            # Only grade if game is Final (skip in-progress)
            state = g.get('status', {}).get('detailedState', '')
            if 'Final' in state or 'Game Over' in state or state == 'Completed Early':
                game_pks.append(g['gamePk'])
    stats = {}
    for pk in game_pks:
        try:
            box = json.load(urllib.request.urlopen(
                f'https://statsapi.mlb.com/api/v1/game/{pk}/boxscore', timeout=15))
        except Exception as e:
            print(f'  ⚠ boxscore {pk} failed: {e}')
            continue
        for team_key in ('home', 'away'):
            team = box.get('teams', {}).get(team_key, {})
            for _, player in team.get('players', {}).items():
                name = player.get('person', {}).get('fullName')
                if not name: continue
                pit = player.get('stats', {}).get('pitching', {}) or {}
                bat = player.get('stats', {}).get('batting', {}) or {}
                ip_str = pit.get('inningsPitched', '0.0')
                outs = 0
                try:
                    ip_int, ip_frac = str(ip_str).split('.')
                    outs = int(ip_int) * 3 + int(ip_frac)
                except (ValueError, AttributeError):
                    pass
                stats[name.lower()] = {
                    'ks':    pit.get('strikeOuts', 0) if pit else 0,
                    'bb':    pit.get('baseOnBalls', 0) if pit else 0,
                    'er':    pit.get('earnedRuns', 0) if pit else 0,
                    'h_pit': pit.get('hits', 0) if pit else 0,
                    'outs':  outs,
                    'h_bat': bat.get('hits', 0) if bat else 0,
                }
    return stats, len(game_pks)


def grade_prop(prop: dict, stats_map: dict) -> tuple:
    """Return (result_str, actual_value) — result_str in
    {'Win','Loss','Push', None}. None if can't grade."""
    stat_key = STAT_MAP_MLB.get(prop.get('prop_type'))
    if not stat_key: return None, None
    stats = stats_map.get((prop.get('player_name') or '').lower())
    if not stats: return None, None
    actual = stats.get(stat_key)
    if actual is None: return None, None
    line = prop.get('prop_line')
    if line is None: return None, actual
    line = float(line)
    direction = (prop.get('direction') or '').lower()
    if direction == 'over':
        if actual > line: return 'Win', actual
        if actual < line: return 'Loss', actual
        return 'Push', actual
    else:  # under
        if actual < line: return 'Win', actual
        if actual > line: return 'Loss', actual
        return 'Push', actual


def grade_date(date_str: str, sport: str = 'MLB', dry_run: bool = False) -> dict:
    """Grade all ungraded props for a single date. Returns tally dict."""
    table = SPORT_PROPS_TABLE.get(sport.upper())
    if not table:
        print(f'sport {sport} not supported yet'); return {}
    print(f'=== grade_props · {sport} · {date_str} ===')

    # Fetch player stats first (single MLB API pass)
    stats_map, n_games = fetch_player_stats_for_date(date_str)
    print(f'  loaded {len(stats_map)} player stat rows from {n_games} final games')
    if n_games == 0:
        print(f'  no final games on {date_str} — skipping'); return {}

    # Fetch props for the date. When --force, include already-graded rows
    # too so we can overwrite stale/buggy values from prior grader runs.
    params = {
        'game_date': f'eq.{date_str}',
        'tier': 'in.(PRIME,STRONG,LEAN,SKIP,COVERAGE)',
        'select': 'id,player_name,prop_type,prop_line,direction,tier,conviction,result,final_value',
    }
    if not FORCE_REGRADE:
        params['result'] = 'is.null'
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ, params=params, timeout=15).json()
    print(f'  {len(r)} props to check ({"force-regrade all" if FORCE_REGRADE else "ungraded only"})')

    tally = {'graded': 0, 'skipped_no_stat': 0, 'skipped_no_player': 0, 'errors': 0,
             'W': 0, 'L': 0, 'P': 0}
    for prop in r:
        result, actual = grade_prop(prop, stats_map)
        if result is None:
            if stats_map.get((prop.get('player_name') or '').lower()) is None:
                tally['skipped_no_player'] += 1
            else:
                tally['skipped_no_stat'] += 1
            continue
        tally['graded'] += 1
        tally[result[0]] += 1  # W/L/P
        if dry_run:
            continue
        # Patch the row
        patch = {'result': result, 'final_value': actual,
                 'resolved_at': datetime.now(timezone.utc).isoformat()}
        r2 = requests.patch(
            f'{SB}/rest/v1/{table}?id=eq.{prop["id"]}',
            headers=H_WRITE, data=json.dumps(patch), timeout=10,
        )
        if r2.status_code not in (200, 204):
            tally['errors'] += 1

    print(f'  graded {tally["graded"]}: {tally["W"]}W {tally["L"]}L {tally["P"]}P')
    dec = tally['W'] + tally['L']
    if dec:
        print(f'  hit rate: {100*tally["W"]/dec:.1f}%')
    if tally['skipped_no_player']:
        print(f'  ⚠ {tally["skipped_no_player"]} props skipped — player not found in boxscore (postponed / late scratch?)')
    if tally['skipped_no_stat']:
        print(f'  ⚠ {tally["skipped_no_stat"]} props skipped — stat not found (unusual)')
    if tally['errors']:
        print(f'  ⚠ {tally["errors"]} DB write errors')
    return tally


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=yesterday_et())
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--backfill', type=int, default=0,
                    help='Backfill this many days ending at --date (default 0 = single date)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='Regrade already-graded rows too (overwrites stale/buggy grades from prior graders)')
    args = ap.parse_args()

    global FORCE_REGRADE
    FORCE_REGRADE = args.force

    if args.backfill > 0:
        end_date = datetime.strptime(args.date, '%Y-%m-%d')
        for i in range(args.backfill):
            d = (end_date - timedelta(days=i)).strftime('%Y-%m-%d')
            grade_date(d, args.sport, args.dry_run)
            print()
    else:
        grade_date(args.date, args.sport, args.dry_run)


if __name__ == '__main__':
    main()
