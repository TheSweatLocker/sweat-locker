"""Post-game grader for prop_jerry_reads.

Trivial — inherits result from the matching mlb_pipeline_props row
(which the prop pipeline already grades). No new grading logic
needed since prop_jerry_reads shares the natural key with the props
table.

For BACK verdicts: result = Win if prop hit, Loss if missed.
For FADE verdicts: result = Win if prop MISSED (fade cashed),
                             Loss if prop hit (fade failed).
For PASS verdicts: result = NO_ACTION (Jerry didn't bet).

Sport-universal via PROPS_TABLE registry (MLB only for now; NBA/NFL
when their prop pipelines ship).

Usage:
    python grade_prop_jerry_reads.py [--date YYYY-MM-DD] [--dry-run]
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

PROPS_TABLE = {
    'MLB': 'mlb_pipeline_props',
    # 'NBA': 'nba_pipeline_props',
    # 'NFL': 'nfl_pipeline_props',
}


def yesterday_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=28)).strftime('%Y-%m-%d')


def flip_for_fade(prop_result: str, verdict: str) -> str:
    """FADE cashes when the underlying prop misses. Flip W↔L."""
    verdict = (verdict or '').upper()
    if verdict != 'FADE':
        return prop_result   # BACK: inherit as-is
    if prop_result == 'Win':  return 'Loss'
    if prop_result == 'Loss': return 'Win'
    return prop_result  # Push/Void pass through


def run_for_sport(sport: str, gd: str, dry_run: bool = False) -> int:
    props_table = PROPS_TABLE.get(sport)
    if not props_table:
        print(f'  [{sport}] no props table registered — skip')
        return 0

    r = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
                     headers=H_READ,
                     params={'sport': f'eq.{sport}', 'game_date': f'eq.{gd}',
                             'result': 'is.null',
                             'select': 'id,game_id,player_name,prop_type,direction,call_verdict'},
                     timeout=15)
    reads = r.json() if r.status_code == 200 else []
    if not reads:
        print(f'  [{sport}] no ungraded prop_jerry_reads on {gd}')
        return 0

    graded = 0
    for read in reads:
        verdict = (read.get('call_verdict') or '').upper()
        if verdict == 'PASS':
            if dry_run:
                print(f'  [DRY] id={read["id"]} PASS → NO_ACTION'); graded += 1
                continue
            requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{read["id"]}',
                           headers=H_WRITE,
                           json={'result': 'NO_ACTION',
                                 'resolved_at': datetime.now(timezone.utc).isoformat()},
                           timeout=10)
            graded += 1
            continue

        # Look up matching prop row
        pr = requests.get(f'{SB}/rest/v1/{props_table}',
                          headers=H_READ,
                          params={'game_id': f'eq.{read["game_id"]}',
                                  'player_name': f'eq.{read["player_name"]}',
                                  'prop_type': f'eq.{read["prop_type"]}',
                                  'direction': f'eq.{read["direction"]}',
                                  'game_date': f'eq.{gd}',
                                  'select': 'result,actual_pa'},
                          timeout=10)
        prop_rows = pr.json() if pr.status_code == 200 else []
        if not prop_rows or prop_rows[0].get('result') in (None, 'Pending'):
            continue  # still pending

        base_result = prop_rows[0]['result']
        final_result = flip_for_fade(base_result, verdict)
        actual = {'prop_result': base_result, 'actual_pa': prop_rows[0].get('actual_pa')}

        if dry_run:
            print(f'  [DRY] id={read["id"]} {read["player_name"]} {verdict} → {final_result}'); graded += 1
            continue
        pu = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{read["id"]}',
                            headers=H_WRITE,
                            json={'result': final_result,
                                  'resolved_at': datetime.now(timezone.utc).isoformat()},
                            timeout=10)
        if pu.status_code in (200, 204):
            graded += 1

    print(f'  [{sport}] graded {graded}/{len(reads)} prop_jerry_reads')
    return graded


def main(game_date: str | None = None, dry_run: bool = False):
    gd = game_date or yesterday_et()
    print(f'=== grade_prop_jerry_reads · {gd}{" · dry-run" if dry_run else ""} ===')
    total = 0
    for sport in PROPS_TABLE.keys():
        total += run_for_sport(sport, gd, dry_run=dry_run)
    print(f'\n=== graded {total} prop_jerry_reads total ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    main(game_date=args.date, dry_run=args.dry_run)
