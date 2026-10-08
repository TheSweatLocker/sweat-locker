"""Re-check already-graded POTD results against the prop row, and correct them.

WHY (2026-10-08)
----------------
Andy, two mornings running: "yesterday POTD is graded incorrectly in sweat
card recap, second day in a row".

`grade_potd` could not see the pick. `jerry_cache.best_bet_<date>.data` holds
six keys — matchup, narrative, pipelineGenerated, result, score, sport — and
none of them is the pick. Every lookup for leanDisplay / pick / call_text /
call_side returned '', so the grader decided Win or Loss without ever reading
what was bet. It is right only when the POTD is a plain side and the
game-level fallback happens to agree; it is wrong whenever the POTD is a
player prop, which is most days:

    10-06  Yamamoto Over 17.5 Outs   threw 21  -> graded Loss   (a WIN)
    10-07  Mahle    Over 13.5 Outs   threw 19  -> graded Loss   (a WIN)

`mlb_pipeline_props` had both right the whole time (outs_over, final_value
21 and 19, result Win), and so did `public_receipts`. Only this path — the
one feeding the app's recap — was wrong.

grade_potd now recovers the pick from `daily_best_bet_history.lean`. But that
alone does not repair history, because grade_potd RETURNS EARLY on any date
whose cached result is already terminal:

    if data.get('result') and data.get('result') != 'no-pick': ... return

That early return is what let a wrong grade become permanent. A grade that
cannot be re-examined cannot be corrected, and two days of wrong results sat
in front of users because of it.

WHAT THIS DOES
Re-derives the result for every POTD in the window from the PROP ROW, which
is the graded source of truth, and rewrites the three places that disagree:
`jerry_cache`, `daily_best_bet_history`, and the `potd` receipt.

IT ONLY EVER CORRECTS A DISAGREEMENT WITH A GRADED PROP ROW.
  * no prop row, or prop row ungraded -> skipped, never guessed
  * computed result equals stored     -> untouched
  * non-prop POTDs                    -> skipped entirely; this tool makes no
                                         claim about sides or totals
That keeps it inside Andy's standing rule — do not mess with records — by
changing a record only when an independent graded source proves it wrong.

CLI
    python reconcile_potd_grades.py --days 30            # dry
    python reconcile_potd_grades.py --days 30 --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import grade_potd as gp

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H_R, H_W = gp.SB, gp.H_R, gp.H_W
TERMINAL = ('Win', 'Loss', 'Push')


def _jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def reconcile(date_str: str, apply: bool) -> str:
    row = gp._fetch_potd(date_str)
    if not row:
        return 'no-row'
    data = _jl(row.get('data'))
    stored = (data.get('result') or '').strip()

    pick = gp._extract_pick_from_potd(data, date_str)
    if pick.get('call_market') != 'prop' or not pick.get('prop_info'):
        return 'not-a-prop'

    computed = gp._grade_prop_via_pipeline(
        pick['sport'], pick['game_id'], date_str, pick['prop_info'])
    if computed not in TERMINAL:
        return 'prop-ungraded'
    if stored == computed:
        return 'agrees'

    pi = pick['prop_info']
    print(f'  {date_str}  {pi["player_name"]} {pi["direction"]} '
          f'{pi["line"]} {pi["prop_type"]}')
    print(f'      stored {stored or "(none)"!r}  ->  CORRECT {computed!r}')
    if not apply:
        return 'would-fix'

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    ok = []
    # 1. jerry_cache — the blob grade_potd reads first and trusts
    nd = dict(data)
    nd['result'] = computed
    nd['graded_at'] = now
    nd['grade_note'] = (f'reconciled from {pi["prop_type"]} '
                        f'{pi["line"]} prop row')
    r = requests.patch(f'{SB}/rest/v1/jerry_cache', headers=H_W,
                       params={'cache_key': f'eq.best_bet_{date_str}'},
                       json={'data': nd}, timeout=20)
    ok.append(('jerry_cache', r.status_code))
    # 2. daily_best_bet_history — what the app's recap reads
    r = requests.patch(f'{SB}/rest/v1/daily_best_bet_history', headers=H_W,
                       params={'bet_date': f'eq.{date_str}'},
                       json={'result': computed, 'resolved_at': now},
                       timeout=20)
    ok.append(('daily_best_bet_history', r.status_code))
    # 3. the potd receipt. `result` is mutable by design on public_receipts
    #    (only identity + pick fields are frozen), so this is permitted.
    r = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                       params={'game_date': f'eq.{date_str}',
                               'surface': 'eq.potd'},
                       json={'result': computed.upper(), 'graded_at': now},
                       timeout=20)
    ok.append(('public_receipts', r.status_code))
    print(f'      wrote: {ok}')
    return 'fixed'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    today = dt.datetime.now(dt.timezone.utc).date()
    counts = {}
    print(f'=== reconcile_potd_grades · {args.days}d · '
          f'{"APPLY" if args.apply else "DRY"} ===')
    for i in range(1, args.days + 1):
        d = (today - dt.timedelta(days=i)).isoformat()
        r = reconcile(d, args.apply)
        counts[r] = counts.get(r, 0) + 1
    print()
    print('  ' + ' · '.join(f'{k}={v}' for k, v in sorted(counts.items())))


if __name__ == '__main__':
    main()
