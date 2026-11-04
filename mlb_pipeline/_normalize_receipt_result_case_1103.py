"""Put public_receipts.result on one vocabulary.

== WHY ====================================================================
Two writers into one column disagreed about case. Inside the SAME function
in grade_public_receipts.py:

    inherit pass        norm = _RESULT_MAP.get(g.strip().lower())   -> 'WIN'
    box-score fallback  patches.append((r['id'], res))              verbatim

and settle_prop_receipts.settle() returns TITLE case ('Win'/'Loss' at lines
483 and 518, 'Push' at 481/516) while this column's convention is UPPER.

Measured state before this ran:

    'NO_ACTION' 14130   'VOID' 9952   'WIN' 7100   'LOSS' 5876
    'UNGRADEABLE' 523   None 428      'PUSH' 50
    'Loss' 199          'Win' 171     'NO PLAY' 5   'TRACKED_ELSEWHERE' 1

375 minority rows: 369 surface='prop_jerry' (NFL 276, NBA 93) graded on
11-02 and 11-03, plus 5 potd 'NO PLAY' and 1 sweat_card.

== NOTHING USER-FACING IS WRONG TODAY, AND I CHECKED =====================
compute_surface_records lowercases before comparing (line 212),
aggregate_daily_records uppercases at all three of its sites (301, 475, 633)
and audit_live_surface_roi uppercases before testing WON/LOST. So the
records Andy sees are correct and this is NOT a live miscount.

It is still worth closing: `result` is compared at ~520 sites in this repo,
and any one that forgets to normalize silently drops these rows -- the same
shape as feedback_result_column_is_title_case, which was about the opposite
table. One vocabulary in the column removes the whole class.

'NO PLAY' -> 'NO_ACTION' is a spelling merge, not a grade change: both mean
no action was taken, and _RESULT_MAP already mapped 'void'/'voided' to
NO_ACTION the same way.

NOT touched: None (428 ungraded -- a grader's job, not a rename),
'TRACKED_ELSEWHERE' (1) and 'UNGRADEABLE' (523) are their own states.

Readback-verified in batches.

    python _normalize_receipt_result_case_1103.py            # dry run
    python _normalize_receipt_result_case_1103.py --apply
"""
import collections
import json
import os
import sys
from pathlib import Path

import requests

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

APPLY = '--apply' in sys.argv

#: exact stored value -> canonical. Only case/spelling, never a grade.
REMAP = {
    'Win': 'WIN',
    'Loss': 'LOSS',
    'Push': 'PUSH',
    'Void': 'VOID',
    'NO PLAY': 'NO_ACTION',
}


def page(table: str, select: str) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=90,
                         params={'select': select, 'limit': 1000,
                                 'offset': off})
        r.raise_for_status()
        b = r.json() or []
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def main() -> int:
    rows = page('public_receipts', 'id,surface,sport,game_date,result')
    before = collections.Counter(repr(r.get('result')) for r in rows)
    todo = [r for r in rows if r.get('result') in REMAP]
    print(f'public_receipts: {len(rows)} rows')
    print('current vocabulary:')
    for v, n in sorted(before.items(), key=lambda x: -x[1]):
        print(f'     {v:22} {n}')
    print()
    print(f'rows to renormalize: {len(todo)}')
    by = collections.Counter(
        (r['result'], REMAP[r['result']], str(r.get('surface')),
         str(r.get('sport'))) for r in todo)
    for (old, new, surf, sp), n in sorted(by.items(), key=lambda x: -x[1]):
        print(f'     {old!r:10} -> {new:10} {surf:12} {sp:6} x{n}')
    if not APPLY:
        print('\n(dry run -- pass --apply to write)')
        return 0
    if not todo:
        print('nothing to do')
        return 0

    rc = 0
    done = 0
    # group by target value so each PATCH is one call per (old -> new) chunk
    groups = collections.defaultdict(list)
    for r in todo:
        groups[r['result']].append(r['id'])
    print()
    for old, ids in groups.items():
        new = REMAP[old]
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            q = ','.join(str(x) for x in chunk)
            pr = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                                timeout=60, params={'id': f'in.({q})'},
                                data=json.dumps({'result': new}))
            if pr.status_code not in (200, 204):
                print(f'  !! PATCH {old}->{new} [{len(chunk)}] '
                      f'{pr.status_code}: {pr.text[:160]}')
                rc = 1
                continue
            rb = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                              timeout=60,
                              params={'select': 'id,result',
                                      'id': f'in.({q})'})
            got = rb.json() or []
            bad = [x for x in got if x.get('result') != new]
            if bad:
                print(f'  !! NOT CONFIRMED: {len(bad)} of {len(chunk)} still '
                      f'{bad[0].get("result")!r}')
                rc = 1
            else:
                done += len(chunk)
                print(f'  confirmed {old!r} -> {new}  x{len(chunk)}')

    after = collections.Counter(
        repr(r.get('result'))
        for r in page('public_receipts', 'result'))
    print(f'\nrenormalized {done} of {len(todo)}')
    print('vocabulary now:')
    for v, n in sorted(after.items(), key=lambda x: -x[1]):
        print(f'     {v:22} {n}')
    return rc


if __name__ == '__main__':
    sys.exit(main())
