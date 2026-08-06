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
    'NFL': 'nfl_pipeline_props',      # enabled 2026-08-03 Sprint 2
    # 'NBA': 'nba_pipeline_props',
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

        # Normalize prop_type (2026-08-02): Jerry synth sometimes emits the
        # bare prop family ("bb", "ha", "outs") without the direction suffix,
        # while mlb_pipeline_props always stores "bb_over"/"bb_under"/etc.
        # Was killing 119 BACK/FADE per day silently.
        jerry_ptype = read['prop_type']
        direction = (read.get('direction') or '').lower()
        opposite = 'under' if direction == 'over' else ('over' if direction == 'under' else None)
        if jerry_ptype and '_' not in jerry_ptype and direction in ('over', 'under'):
            family = jerry_ptype
        elif jerry_ptype and '_' in jerry_ptype:
            family = jerry_ptype.rsplit('_', 1)[0]  # ks_over → ks
        else:
            family = jerry_ptype

        lookup_same = f'{family}_{direction}' if direction else family
        lookup_flip = f'{family}_{opposite}' if opposite else None
        flipped = False

        # Try exact match (same family, same direction)
        pr = requests.get(f'{SB}/rest/v1/{props_table}',
                          headers=H_READ,
                          params={'game_id': f'eq.{read["game_id"]}',
                                  'player_name': f'eq.{read["player_name"]}',
                                  'prop_type': f'eq.{lookup_same}',
                                  'direction': f'eq.{direction}',
                                  'game_date': f'eq.{gd}',
                                  'select': 'result,final_value'},
                          timeout=10)
        if pr.status_code != 200:
            print(f'  ⚠ id={read["id"]} lookup HTTP {pr.status_code}: {pr.text[:200]}')
            prop_rows = []
        else:
            prop_rows = pr.json()

        # If no same-direction row, try opposite direction (grade with flip)
        if not prop_rows and lookup_flip:
            pr2 = requests.get(f'{SB}/rest/v1/{props_table}',
                               headers=H_READ,
                               params={'game_id': f'eq.{read["game_id"]}',
                                       'player_name': f'eq.{read["player_name"]}',
                                       'prop_type': f'eq.{lookup_flip}',
                                       'direction': f'eq.{opposite}',
                                       'game_date': f'eq.{gd}',
                                       'select': 'result,final_value'},
                               timeout=10)
            prop_rows = pr2.json() if pr2.status_code == 200 else []
            if prop_rows:
                flipped = True  # Jerry called opposite side — flip result

        # No matching prop at all — mark UNGRADEABLE so it stops being Pending
        # forever. Coverage sweeper missed this prop entirely, or Jerry read
        # against a family we don't track. Preserves auditability.
        if not prop_rows:
            if dry_run:
                print(f'  [DRY] id={read["id"]} {read["player_name"]} {jerry_ptype}/{direction} → UNGRADEABLE (no pipeline row)')
                graded += 1
                continue
            requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{read["id"]}',
                           headers=H_WRITE,
                           json={'result': 'UNGRADEABLE',
                                 'resolved_at': datetime.now(timezone.utc).isoformat()},
                           timeout=10)
            graded += 1
            continue

        if prop_rows[0].get('result') in (None, 'Pending'):
            continue  # underlying prop still pending

        base_result = prop_rows[0]['result']
        # If we matched on OPPOSITE direction (Jerry called over, pipeline stored
        # under), flip the base_result first — the underlying outcome is the same
        # (player's actual K/BB/HA count) but Win/Loss reads inverse from the
        # opposite line. Then apply FADE flip on top of that if verdict is FADE.
        if flipped:
            if base_result == 'Win':  base_result = 'Loss'
            elif base_result == 'Loss': base_result = 'Win'
        final_result = flip_for_fade(base_result, verdict)
        actual = {'prop_result': base_result, 'final_value': prop_rows[0].get('final_value')}

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


def main(game_date: str | None = None, dry_run: bool = False,
         backfill_days: int = 3):
    """Grade prop_jerry_reads. Default runs on yesterday + backfill_days prior
    to catch late-arriving results (games delayed, rows created after cron,
    grader schedule misses).

    Coverage audit 2026-08-06 found 2 ungraded rows on 8/1 that survived the
    1-day-only grader (previous behavior). Backfill window catches those.
    """
    if game_date:
        # Explicit date — just that one
        dates = [game_date]
    else:
        # Yesterday + N days prior for backfill of any straggling rows
        base = yesterday_et()
        from datetime import date, timedelta
        base_d = date.fromisoformat(base)
        dates = [(base_d - timedelta(days=i)).isoformat() for i in range(backfill_days + 1)]

    total = 0
    for gd in dates:
        print(f'=== grade_prop_jerry_reads · {gd}{" · dry-run" if dry_run else ""} ===')
        for sport in PROPS_TABLE.keys():
            total += run_for_sport(sport, gd, dry_run=dry_run)
    print(f'\n=== graded {total} prop_jerry_reads total across {len(dates)} date(s) ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='Specific date; if omitted, grades yesterday + backfill window')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--backfill-days', type=int, default=3,
                   help='Days prior to yesterday to also re-grade (catches late-arriving results). Default 3.')
    args = p.parse_args()
    main(game_date=args.date, dry_run=args.dry_run, backfill_days=args.backfill_days)
