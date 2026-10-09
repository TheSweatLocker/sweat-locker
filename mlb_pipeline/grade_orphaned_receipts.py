"""Grade a receipt whose source row was deleted, from the measured prop row.

WHY (2026-10-08)
----------------
Receipts are the product's accountability claim, so a receipt sitting ungraded
on a game that finished weeks ago is the most expensive kind of gap. Measured
321 of them. The breakdown:

    MLB/prop_jerry   210      by age:  2026-06    7
    MLB/sweat_card    42               2026-07    6
    NFL/game_read     26               2026-08   37
    NFL/prop_jerry    24               2026-09  311
    MLB/sharp_card    21               2026-10   16
    ...

234 of the 321 point at `prop_jerry_reads` — and THE SOURCE ROWS ARE GONE.
Sampled 40 of their `source_id`s and found 0, with every id inside the live
range (34..136969), so these were deleted after the receipts were written, not
mis-stamped:

    101750 -> MISSING   109148 -> MISSING   110333 -> MISSING

`grade_public_receipts` resolves a prop receipt by following source_id into the
source table, so a deleted source row makes the receipt permanently
ungradeable. It reports `source_row_ungraded` and moves on, forever. Something
deletes prop_jerry_reads rows (regeneration / dedupe) while public_receipts
holds a pointer nothing maintains.

THE KEY INSIGHT: the receipt does not need its source row. `pick_label` carries
the whole bet — "Tyler Mahle Over 13.5 Outs (Jerry 85/100)" — and
`reconcile_receipt_results.truth_from_props()` already resolves exactly that
shape against the GRADED prop row in <sport>_pipeline_props, which is the only
place an actually measured stat lives. So the result is recoverable from
identity even though the pointer is dead.

This imports that function rather than reimplementing it, so there is one
definition of "what did this prop actually do" instead of two that can drift.

WHAT IT WILL NOT DO
  * only touches receipts whose `result` IS NULL on a game_date in the past
  * never writes a result it cannot measure — no graded prop row means the
    receipt is reported and left alone, never guessed
  * never touches identity or pick fields (the 20260918b freeze would revert
    them anyway); `result` and `graded_at` are mutable by design
  * does not try to fix the orphaning itself. Stopping prop_jerry_reads rows
    from being deleted out from under live receipts is a separate change to
    whatever deletes them, and guessing at that here would be the band-aid.

CLI
    python grade_orphaned_receipts.py --days 180
    python grade_orphaned_receipts.py --days 180 --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML
from reconcile_receipt_results import _key, truth_from_props

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}


def _page(t, p, cap=60000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:140]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=180)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    today = dt.date.today()
    since = (today - dt.timedelta(days=a.days)).isoformat()
    rc = _page('public_receipts',
               {'select': 'id,sport,surface,game_date,pick_label,result,'
                          'source_table,source_id',
                'result': 'is.null',
                'and': f'(game_date.gte.{since},'
                       f'game_date.lt.{today.isoformat()})'})
    print(f'=== {len(rc)} receipts ungraded on finished games since {since} '
          f'· {"APPLY" if a.apply else "DRY"} ===')

    # Which of these are orphaned — pointer present, source row gone?
    bysrc = collections.defaultdict(list)
    for x in rc:
        if x.get('pick_label') and x.get('source_table') and x.get('source_id'):
            bysrc[str(x['source_table'])].append(x)

    orphan = []
    for tbl, rows in bysrc.items():
        base = tbl.split('.')[0]
        ids = sorted({str(r['source_id']) for r in rows
                      if str(r['source_id']).isdigit()})
        if not ids:
            continue
        alive = set()
        for i in range(0, len(ids), 150):
            chunk = ids[i:i + 150]
            got = _page(base, {'select': 'id',
                               'id': 'in.(' + ','.join(chunk) + ')'})
            alive |= {str(x['id']) for x in got}
        gone = [r for r in rows if str(r['source_id']) not in alive]
        print(f'  {tbl:<28}{len(rows):>4} receipts · '
              f'source row MISSING on {len(gone)}')
        orphan += gone

    print(f'\n  {len(orphan)} orphaned receipt(s) — pointer dead, '
          f'recovering from the graded prop row by identity')
    fixed = unresolved = unparsed = 0
    truth_cache: dict = {}
    for r in sorted(orphan, key=lambda z: str(z['game_date'])):
        k = _key(r)
        if not k:
            unparsed += 1
            continue
        if k not in truth_cache:
            truth_cache[k] = truth_from_props(k)
        truth, why = truth_cache[k]
        if not truth:
            unresolved += 1
            continue
        print(f"  {r['game_date']}  {r['sport']:<5}{r['surface']:<12}"
              f"{str(r['pick_label'])[:46]:<48}-> {truth}")
        fixed += 1
        if not a.apply:
            continue
        pr = requests.patch(f"{SB}/rest/v1/public_receipts?id=eq.{r['id']}",
                            headers=H_W,
                            data=json.dumps({
                                'result': truth,
                                'graded_at': dt.datetime.now(
                                    dt.timezone.utc).isoformat()}),
                            timeout=60)
        if pr.status_code not in (200, 204):
            print(f'      ! patch {pr.status_code} {pr.text[:120]}')

    print(f'\n  {"graded" if a.apply else "gradeable"} {fixed}')
    print(f'  no graded prop row (left alone, not guessed): {unresolved}')
    print(f'  pick_label unparseable: {unparsed}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
