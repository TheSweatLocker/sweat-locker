"""Re-file NCAAB rows under the date their game is actually played.

== 2026-11-02 ==============================================================
ncaab_game_context.py built every row with `"game_date": today_et()`. The
Odds API /odds endpoint returns every UPCOMING event, so each one was filed
under the day the job ran.

MEASURED, the whole table (_ncaab_datebug.py):
    138 rows, ALL stamped game_date = 2026-11-01
    136 of 138 disagree with their own game_time_et
    real tip dates span 2026-11-01 .. 2027-03-06
    Duke @ Florida tips 2026-12-01 and was filed as 11-01

THE CONVENTION IS NOT IN DOUBT. Asked of the three sports that work
(_convention.py), game_date is the PLAY date, not the publish date:
    NBA   ctx 188/188 match the ET date of commence_time_utc
    NHL   ctx 327/327
    NFL   ctx 301/316
    receipts matching the PLAY date: NBA 171/171, NHL 145/145, NFL 1599/1599
    receipts matching the PUBLISH date: 77, 47, 170 respectively
NCAAB is the only sport that breaks it.

NO WRONG GRADES EXIST. Every NCAAB receipt carrying a result is VOID -- the
sixteen fabricated-line rows voided yesterday -- and graded_at is NULL on
all of them. So this is a clerical repair, not a record repair, which is
why it can run without a sign-off. A row with a real grade would need its
own path (feedback_grade_each_row_not_the_date).

WHAT IT TOUCHES, and what it refuses to:
  * ncaab_game_context  -- derived, rebuilt by every run. Repaired.
  * jerry_reads         -- repaired only where result IS NULL.
  * public_receipts     -- repaired only where result IS NULL. A VOID or
                          graded receipt is a settled record and is left
                          exactly as it stands.
Every PATCH is BY id and read back.

  python _repair_ncaab_game_date_1102.py
  python _repair_ncaab_game_date_1102.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

for _l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}
H_W = dict(H, **{'Content-Type': 'application/json',
                 'Prefer': 'return=representation'})

sys.path.insert(0, str(Path(__file__).parent))
from sweat_dates import et_date_of                             # noqa: E402

#: Results that are a real settlement and must never be re-filed under a
#: different date. NO_ACTION is deliberately NOT here: it is what a PASS read
#: carries (call_market='pass', conviction=0, actual_outcome scores NULL), so
#: it records that no play was made rather than how one finished.
#:
#: Measured on this table: every one of the 71 NCAAB reads holding a result
#: holds NO_ACTION, 69 of them for games tipping tonight, all resolved at
#: 2026-11-02T08:12 -- BEFORE tip, because the grader was handed 11-01 as
#: "yesterday" and these games are on the 2nd. Harmless on a pass; it would
#: not have been on a play. The date repair is what removes that hazard, and
#: it is why NO_ACTION must not be mistaken for a settled record here.
SETTLED = ('WIN', 'LOSS', 'PUSH', 'VOID')


def is_settled(row) -> bool:
    return str(row.get('result') or '').upper() in SETTLED


NOTE = ('game_date was the job run date, not the play date '
        '(ncaab_game_context.py used today_et()). Re-filed 2026-11-02 from '
        'game_time_et via sweat_dates.et_date_of.')


def page(table, params):
    out, off = [], 0
    while off < 100000:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=180,
                         params=dict(params, limit=1000, offset=off))
        if r.status_code != 200:
            print(f'  !! {table} HTTP {r.status_code} {r.text[:250]}')
            return out, False
        b = r.json()
        out += b
        if len(b) < 1000:
            return out, True
        off += 1000
    return out, False


def patch(table, row_id, body):
    r = requests.patch(f'{SB}/rest/v1/{table}', headers=H_W,
                       params={'id': f'eq.{row_id}'}, json=body, timeout=60)
    back = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                        params={'select': 'game_date', 'id': f'eq.{row_id}'},
                        timeout=60)
    got = (back.json()[0].get('game_date')
           if back.status_code == 200 and back.json() else None)
    return r.status_code, got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=== re-file NCAAB rows by play date - %s ===\n'
          % ('APPLY' if a.apply else 'DRY RUN'))

    ctx, full = page('ncaab_game_context',
                     {'select': 'id,game_id,game_date,game_time_et,'
                                'home_team,away_team'})
    if not ctx:
        print('  !! read 0 ncaab_game_context rows - WIRING ERROR, not a '
              'finding. Nothing attempted.')
        return 1
    if not full:
        print('  !! paging incomplete - refusing to act on a truncated read.')
        return 1
    print(f'ncaab_game_context rows read: {len(ctx)} (complete)')

    TRUE, no_tip = {}, []
    for r in ctx:
        d = et_date_of(r.get('game_time_et'))
        if not d:
            no_tip.append(r)
            continue
        TRUE[r['game_id']] = d
    if no_tip:
        print(f'  {len(no_tip)} rows have no usable game_time_et - LEFT ALONE '
              f'(not guessing a date)')

    # -- ncaab_game_context ------------------------------------------------
    todo = [r for r in ctx
            if r['game_id'] in TRUE and r['game_date'] != TRUE[r['game_id']]]
    print(f'\n[1/3] ncaab_game_context mis-filed: {len(todo)} of {len(ctx)}')
    for r in todo[:10]:
        print(f"      {str(r['away_team'])[:24]:24s} @ "
              f"{str(r['home_team'])[:20]:20s} "
              f"{r['game_date']} -> {TRUE[r['game_id']]}")
    if len(todo) > 10:
        print(f'      ... and {len(todo)-10} more')
    print('      target date spread: %s'
          % dict(sorted(Counter(TRUE[r['game_id']] for r in todo).items())))

    # -- jerry_reads (ungraded only) ---------------------------------------
    jr, jrfull = page('jerry_reads', {'select': 'id,game_id,game_date,result',
                                      'sport': 'eq.NCAAB'})
    if not jrfull:
        print('  !! jerry_reads paging incomplete - refusing.')
        return 1
    jr_todo = [r for r in jr
               if r['game_id'] in TRUE and r['game_date'] != TRUE[r['game_id']]
               and not is_settled(r)]
    jr_kept = [r for r in jr
               if r['game_id'] in TRUE and r['game_date'] != TRUE[r['game_id']]
               and is_settled(r)]
    print(f'\n[2/3] jerry_reads NCAAB: {len(jr)} rows, mis-filed and '
          f'unsettled {len(jr_todo)}, mis-filed but SETTLED (left alone) '
          f'{len(jr_kept)}')
    print('      unsettled, by the result they currently hold: %s'
          % dict(Counter(str(r.get('result')) for r in jr_todo)))

    # -- public_receipts (ungraded only) -----------------------------------
    pr, prfull = page('public_receipts',
                      {'select': 'id,game_id,game_date,surface,market,result,'
                                 'audit', 'sport': 'eq.NCAAB'})
    if not prfull:
        print('  !! public_receipts paging incomplete - refusing.')
        return 1
    pr_todo = [r for r in pr
               if r['game_id'] in TRUE and r['game_date'] != TRUE[r['game_id']]
               and not is_settled(r)]
    pr_kept = [r for r in pr
               if r['game_id'] in TRUE and r['game_date'] != TRUE[r['game_id']]
               and is_settled(r)]
    print(f'[3/3] public_receipts NCAAB: {len(pr)} rows, mis-filed and '
          f'unsettled {len(pr_todo)}, mis-filed but SETTLED (left alone) '
          f'{len(pr_kept)}')
    if pr_kept:
        print('      settled results left alone: %s'
              % dict(Counter(str(r['result']).upper() for r in pr_kept)))

    if not a.apply:
        print('\n  DRY RUN - nothing written. Re-run with --apply.')
        return 0

    ok = bad = 0
    print('\n--- applying ---')
    for label, table, rows in (('ctx', 'ncaab_game_context', todo),
                               ('read', 'jerry_reads', jr_todo),
                               ('receipt', 'public_receipts', pr_todo)):
        for r in rows:
            want = TRUE[r['game_id']]
            body = {'game_date': want}
            if table == 'public_receipts':
                body['audit'] = dict(r.get('audit') or {},
                                     **{'refiled_at': '2026-11-02',
                                        'refiled_from': r['game_date'],
                                        'refiled_reason': NOTE})
            sc, got = patch(table, r['id'], body)
            good = got == want
            ok += 1 if good else 0
            bad += 0 if good else 1
            if not good:
                print(f'   x {label} id={r["id"]} HTTP {sc} readback={got!r} '
                      f'wanted {want!r}')
        print(f'   {label:8s}: {len(rows)} attempted')
    print(f'\n  repaired {ok}, failed {bad}')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
