"""Repair FADE prop receipts that display the prop's side, not the backed side.

Andy 2026-09-25: "alter if it benefits the record."

THE BUG
On a FADE read we bet the OPPOSITE side of the prop, but
backfill_public_receipts wrote `pick_side = direction` — the prop's own side —
regardless of verdict. So a receipt reads

    "Jose Quintana UNDER 10.5 outs_under @ -118"    result: WIN

while the WIN belongs to the OVER, because grade_prop_jerry_reads already
flips for FADE when grading (flip_for_fade). The result was right and only the
label was wrong, which is the hardest version to notice — the receipt is
internally contradictory and a user reading it sees the wrong pick.

Found while validating settle_prop_receipts: re-settling graded receipts from
the box score disagreed on exactly four rows, and every one was a FADE.

Writer fixed separately; this repairs the rows already written.

IDEMPOTENCY — the thing that makes a flip script safe to rerun.
prop_type already encodes the prop's direction ('outs_under' -> under). A row
still needing repair has pick_side EQUAL to that implied direction. A row
already repaired has pick_side OPPOSITE it. So the script can tell them apart
without a marker column, and running twice is a no-op rather than a flip-back.

Only pick_side and pick_label change. result, odds, line and grading are
untouched — the result was never wrong.

    python fix_fade_receipt_sides.py             # dry run
    python fix_fade_receipt_sides.py --commit
"""
import argparse
import json
import os
import sys

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

FLIP = {'OVER': 'UNDER', 'UNDER': 'OVER'}


def page(path, params):
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ERROR {r.status_code}: {(r.text or "")[:200]}')
            sys.exit(2)
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def implied_direction(prop_type: str) -> str | None:
    pt = (prop_type or '').lower()
    if pt.endswith('_over'):
        return 'OVER'
    if pt.endswith('_under'):
        return 'UNDER'
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--commit', action='store_true')
    args = ap.parse_args()

    recs = page('public_receipts', {
        'select': 'id,player_name,prop_type,pick_side,pick_line,pick_odds,'
                  'pick_label,result,audit',
        'surface': 'eq.prop_jerry', 'source_table': 'eq.prop_jerry_reads'})

    todo, already, unflippable = [], 0, 0
    for r in recs:
        a = r.get('audit')
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except Exception:
                a = None
        if not isinstance(a, dict):
            continue
        if str(a.get('call_verdict') or '').upper() != 'FADE':
            continue
        side = (r.get('pick_side') or '').upper()
        imp = implied_direction(r.get('prop_type'))
        if side not in FLIP or imp is None:
            unflippable += 1
            continue
        if side != imp:
            already += 1        # already repaired — leave it alone
            continue
        todo.append(r)

    print(f'=== fix_fade_receipt_sides · {"APPLY" if args.commit else "DRY"} ===')
    print(f'  prop receipts scanned      : {len(recs)}')
    print(f'  FADE, needing repair       : {len(todo)}')
    print(f'  FADE, already repaired     : {already}')
    print(f'  FADE, side not flippable   : {unflippable}')
    if not todo:
        print('\n  nothing to do.')
        return

    graded = sum(1 for r in todo if str(r.get('result') or '').upper() in ('WIN', 'LOSS', 'PUSH'))
    print(f'  of those, already graded   : {graded}')
    print()
    for r in todo[:10]:
        old = (r['pick_side'] or '').upper()
        print(f"   id={r['id']:>7} {str(r['player_name'])[:20]:21s} "
              f"{r['prop_type']:12s} {old:5s} -> {FLIP[old]:5s}  "
              f"line={r['pick_line']}  result={r.get('result')}")
    if len(todo) > 10:
        print(f'   … and {len(todo) - 10} more')

    if not args.commit:
        print('\n  DRY RUN — add --commit to write.')
        return

    ok = fail = 0
    for r in todo:
        old = (r['pick_side'] or '').upper()
        new = FLIP[old]
        label = r.get('pick_label') or ''
        # Replace only the standalone side token, so a player name containing
        # "over" cannot be mangled.
        new_label = label.replace(f' {old} ', f' {new} ', 1) if f' {old} ' in label else label
        pr = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                            timeout=30, params={'id': f'eq.{r["id"]}'},
                            json={'pick_side': new, 'pick_label': new_label})
        # 2026-09-25: a 200 is NOT proof of a write here. public_receipts is
        # immutable at the database level — PATCH returns 200 and echoes the
        # OLD row, exactly as publish_lock does on mlb_pipeline_props. The
        # first version of this script counted status codes and reported
        # "repaired 545/545" having changed nothing at all. Read the row back.
        if pr.status_code not in (200, 204):
            fail += 1
            if fail <= 3:
                print(f'  ⚠ id={r["id"]} -> {pr.status_code} {(pr.text or "")[:120]}')
            continue
        chk = requests.get(f'{SB}/rest/v1/public_receipts', headers=H, timeout=30,
                           params={'select': 'pick_side', 'id': f'eq.{r["id"]}'})
        rows = chk.json() if chk.status_code == 200 else []
        got = (rows[0].get('pick_side') if rows else None)
        if str(got).upper() == new:
            ok += 1
        else:
            fail += 1
            print(f'\n  ⚠ WRITE REVERTED on id={r["id"]}: asked for {new}, '
                  f'row still reads {got}.')
            print('    public_receipts is immutable at the DB level, so this '
                  'repair cannot be done\n    with a PATCH. It needs the '
                  'trigger relaxed or a migration. Stopping rather\n    than '
                  'issuing 544 more writes that will also be discarded.')
            break
    print(f'\n  repaired {ok}/{len(todo)}' + (f', {fail} blocked' if fail else ''))
    if ok == 0 and fail:
        print('  NOTHING WAS WRITTEN — verified by reading the row back, '
              'not by trusting the status code.')


if __name__ == '__main__':
    main()
