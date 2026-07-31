"""Post-game grader for jerry_reads (game-level Jerry synthesis).

Runs nightly (or after each game finishes). For every jerry_reads row
where result is null AND the game has resolved, computes W/L/P/void
based on call_market + call_side + call_line vs mlb_game_results.

Sport-universal: dispatches per-sport via RESULTS_TABLE registry
matching the betResolver pattern.

Usage:
    python grade_jerry_reads.py [--date YYYY-MM-DD] [--sport MLB] [--dry-run]
"""
import argparse, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

RESULTS_TABLE = {
    'MLB':   'mlb_game_results',
    'NBA':   'nba_game_results',
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
    # UFC uses fight_results — different shape; excluded from POTD anyway
}


def yesterday_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=28)).strftime('%Y-%m-%d')


def grade_ml(side: str, hs: int, as_: int) -> str:
    if hs == as_: return 'Push'
    won = (side == 'HOME' and hs > as_) or (side == 'AWAY' and as_ > hs)
    return 'Win' if won else 'Loss'


def grade_total(side: str, line, hs: int, as_: int) -> str:
    if line is None: return None
    total = hs + as_
    if total == line: return 'Push'
    if side == 'OVER':  return 'Win' if total > line else 'Loss'
    if side == 'UNDER': return 'Win' if total < line else 'Loss'
    return None


def grade_rl(side: str, line, hs: int, as_: int, run_line_result: str | None) -> str:
    # Prefer server-computed run_line_result when available
    if run_line_result:
        rl = run_line_result.lower()
        if side == 'HOME' and rl == 'home': return 'Win'
        if side == 'AWAY' and rl == 'away': return 'Win'
        return 'Loss'
    if line is None: return None
    # Standard: run line -1.5 means picked side must win by 2+
    margin_picked = (hs - as_) if side == 'HOME' else (as_ - hs)
    return 'Win' if margin_picked > abs(line) else 'Loss'


def grade_one(read: dict, results_by_gid: dict) -> str | None:
    """Return 'Win' | 'Loss' | 'Push' | 'Void' | None (pending)."""
    gid = read.get('game_id')
    market = (read.get('call_market') or '').lower()
    if market == 'pass':
        return 'NO_ACTION'
    res = results_by_gid.get(gid)
    if not res or res.get('home_score') is None:
        return None
    hs, as_ = int(res['home_score']), int(res['away_score'])
    side = (read.get('call_side') or '').upper()
    line = read.get('call_line')
    if market == 'ml':
        return grade_ml(side, hs, as_)
    if market == 'total':
        return grade_total(side, line, hs, as_)
    if market == 'rl':
        return grade_rl(side, line, hs, as_, res.get('run_line_result'))
    return None


def run_for_sport(sport: str, game_date: str, dry_run: bool = False) -> int:
    results_table = RESULTS_TABLE.get(sport)
    if not results_table:
        print(f'  [{sport}] no results table registered — skip')
        return 0

    # Pull unresolved jerry_reads for this sport/date
    r = requests.get(f'{SB}/rest/v1/jerry_reads',
                     headers=H_READ,
                     params={'sport': f'eq.{sport}', 'game_date': f'eq.{game_date}',
                             'result': 'is.null',
                             'select': 'id,game_id,call_market,call_side,call_line'},
                     timeout=15)
    reads = r.json() if r.status_code == 200 else []
    if not reads:
        print(f'  [{sport}] no ungraded jerry_reads on {game_date}')
        return 0

    # Pull results
    gid_list = list({r['game_id'] for r in reads if r.get('game_id')})
    gid_in = ','.join(f'"{g}"' for g in gid_list)
    rr = requests.get(f'{SB}/rest/v1/{results_table}',
                      headers=H_READ,
                      params={'game_id': f'in.({gid_in})',
                              'select': 'game_id,home_score,away_score,run_line_result,total_result'},
                      timeout=15)
    results_by_gid = {row['game_id']: row for row in (rr.json() if rr.status_code == 200 else [])}

    graded = 0
    for read in reads:
        result = grade_one(read, results_by_gid)
        if not result: continue
        actual = {'home_score': results_by_gid.get(read['game_id'], {}).get('home_score'),
                  'away_score': results_by_gid.get(read['game_id'], {}).get('away_score')}
        if dry_run:
            print(f'  [DRY] {sport} id={read["id"]} {read["call_market"]}/{read["call_side"]} → {result}')
            graded += 1
            continue
        pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{read["id"]}',
                            headers=H_WRITE,
                            json={'result': result,
                                  'actual_outcome': actual,
                                  'resolved_at': datetime.now(timezone.utc).isoformat()},
                            timeout=15)
        if pr.status_code in (200, 204):
            graded += 1
        else:
            print(f'  ⚠ patch id={read["id"]}: {pr.status_code} {pr.text[:120]}')
    print(f'  [{sport}] graded {graded}/{len(reads)} jerry_reads')
    return graded


def main(game_date: str | None = None, sport: str | None = None, dry_run: bool = False):
    gd = game_date or yesterday_et()
    print(f'=== grade_jerry_reads · {gd}{" · dry-run" if dry_run else ""} ===')
    sports = [sport] if sport else list(RESULTS_TABLE.keys())
    total = 0
    for s in sports:
        total += run_for_sport(s, gd, dry_run=dry_run)
    print(f'\n=== graded {total} jerry_reads total ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults yesterday ET')
    p.add_argument('--sport', help='MLB/NBA/NFL/NCAAF/NCAAB (default: all)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    main(game_date=args.date, sport=args.sport, dry_run=args.dry_run)
