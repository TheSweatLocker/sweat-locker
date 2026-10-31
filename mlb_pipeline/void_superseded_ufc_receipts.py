"""Four UFC receipts carry a price that is not a price. Void them.

== 2026-10-31 . "IS EVERYTHING SURFACING TO USERS CORRECT" ================
ANDY earlier today: "If picks are incorrectly labeled in app casuing users to
act on somehting that we dont believe in is unacceptable fix it."

generate_sharp_card._compose_ufc read the book's DECIMAL odds and published
them through the American-odds formatter, so 1.375 (really -267) rendered as
"+1.375" and int()-truncated to pick_odds=1. That generator is fixed
(_decimal_to_american, commit 33c41cc3) and the UFC ML juice cap is now
enforced, which re-published the slate correctly at 12:55:

    3982406  Jacobe Smith ML  (-267)      odds=-267   <- correct, ON the Sharp
    3982407  Piera Rodriguez ML  (-294)   odds=-294   <- correct, ON the Sharp

The four 11:45 receipts are still live, and they are wrong in two different
ways:

  SUPERSEDED DUPLICATES -- the same two plays, already in the record at their
  real price. Left alone they double-count, and the duplicate scores at
  pick_odds=1, which is a 0.01u payout on a -267 favourite:
    3982400  'Jacobe Smith ML  (+1.375)'     odds=1
    3982401  'Piera Rodriguez ML  (+1.34)'   odds=1

  WITHDRAWN PLAYS -- these two failed the UFC ML juice cap once their real
  prices were visible (-417 and -308), so they are NOT on The Sharp. Their
  receipts would still grade into our published record:
    3982402  'Zach Reese ML  (+1.24)'        odds=1   really -417
    3982403  'Luana Santos ML  (+1.325)'     odds=1   really -308

WHY VOID AND NOT REPAIR. freeze_receipt_identity (20260918b) makes pick_label
fully frozen and pick_odds set-once, and these rows already hold a non-NULL
pick_odds of 1, so a PATCH to the real price is refused -- silently, with
HTTP 200 and the old row echoed back (feedback_204_is_not_a_write). `result`
is one of the three mutable columns, so VOID is the one legal write here.

Nothing is erased from the record: both plays we stand behind survive at
their true prices in 3982406/3982407. What goes is a duplicate scored at a
fake price and two plays we withdrew.

VOID is spelled in UPPER case in this table -- verified against the live
distinct values (VOID 18, WIN 166, LOSS 168), not assumed.

Confirms by READBACK, never the status code.

USAGE
  python void_superseded_ufc_receipts.py
  python void_superseded_ufc_receipts.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H_R = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}
H_W = dict(H_R)
H_W.update({'Content-Type': 'application/json',
            'Prefer': 'return=representation'})

#: receipt id -> (expected label, why it is being voided, the live replacement)
VOID = {
    3982400: ('Jacobe Smith ML  (+1.375)',
              'decimal odds published as American: pick_odds=1 on a -267 '
              'favourite. Superseded by 3982406 at the real price.',
              3982406),
    3982401: ('Piera Rodriguez ML  (+1.34)',
              'decimal odds published as American: pick_odds=1 on a -294 '
              'favourite. Superseded by 3982407 at the real price.',
              3982407),
    3982402: ('Zach Reese ML  (+1.24)',
              'real price -417, WITHDRAWN by the UFC ML juice cap and not on '
              'The Sharp. Would otherwise grade as a play we do not stand '
              'behind.',
              None),
    3982403: ('Luana Santos ML  (+1.325)',
              'real price -308, WITHDRAWN by the UFC ML juice cap and not on '
              'The Sharp. Would otherwise grade as a play we do not stand '
              'behind.',
              None),
}
GRADE = 'VOID'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('[%s] voiding %d superseded/withdrawn UFC receipts'
          % ('APPLY' if a.apply else 'DRY RUN', len(VOID)))

    failed = 0
    for rid, (want_label, why, replacement) in VOID.items():
        r = requests.get(SB + '/rest/v1/public_receipts', headers=H_R,
                         timeout=60,
                         params={'id': 'eq.%d' % rid,
                                 'select': 'id,surface,pick_label,pick_odds,'
                                           'result,game_date'})
        rows = r.json() if r.status_code == 200 else None
        if rows is None:
            print('  %s: READ FAILED HTTP %s -- SKIPPED, not assumed absent'
                  % (rid, r.status_code))
            failed += 1
            continue
        if not rows:
            print('  %s: not found -- SKIPPED' % rid)
            failed += 1
            continue
        cur = rows[0]
        # identity gate: never void a row that is not the one described above
        if str(cur.get('pick_label')) != want_label:
            print('  %s: label is %r, expected %r -- REFUSING'
                  % (rid, cur.get('pick_label'), want_label))
            failed += 1
            continue
        if str(cur.get('result') or '').upper() in ('VOID', 'VOIDED'):
            print('  %s %r: already %s -- left alone'
                  % (rid, want_label, cur.get('result')))
            continue
        if cur.get('result'):
            print('  %s %r: already graded %r -- REFUSING to overwrite a '
                  'decided result' % (rid, want_label, cur.get('result')))
            failed += 1
            continue

        print('\n  %s  %s  %r  odds=%s -> %s'
              % (rid, cur.get('surface'), want_label, cur.get('pick_odds'),
                 GRADE))
        print('      why: %s' % why)
        if replacement:
            rp = requests.get(SB + '/rest/v1/public_receipts', headers=H_R,
                              timeout=60,
                              params={'id': 'eq.%d' % replacement,
                                      'select': 'id,pick_label,pick_odds,'
                                                'result'})
            rpj = rp.json() if rp.status_code == 200 else []
            if not rpj:
                print('      ! replacement %s NOT FOUND -- REFUSING, the play '
                      'would vanish from the record' % replacement)
                failed += 1
                continue
            q = rpj[0]
            if str(q.get('result') or '').upper() in ('VOID', 'VOIDED'):
                print('      ! replacement %s is itself VOID -- REFUSING'
                      % replacement)
                failed += 1
                continue
            print('      play survives as %s %r odds=%s'
                  % (q['id'], q.get('pick_label'), q.get('pick_odds')))
        if not a.apply:
            continue

        pr = requests.patch(SB + '/rest/v1/public_receipts', headers=H_W,
                            timeout=60, params={'id': 'eq.%d' % rid},
                            json={'result': GRADE,
                                  'graded_at': dt.datetime.now(
                                      dt.timezone.utc).isoformat()})
        back = pr.json() if pr.status_code in (200, 201) else []
        ok = bool(back) and str(back[0].get('result')) == GRADE
        if not ok:                        # readback, never the status code
            gg = requests.get(SB + '/rest/v1/public_receipts', headers=H_R,
                              timeout=60, params={'id': 'eq.%d' % rid,
                                                  'select': 'result'})
            gj = gg.json() if gg.status_code == 200 else []
            ok = bool(gj) and str(gj[0].get('result')) == GRADE
        print('      HTTP %s %s' % (pr.status_code,
                                    'CONFIRMED' if ok else
                                    'NOT CONFIRMED -- nothing changed'))
        failed += 0 if ok else 1

    if not a.apply:
        print('\nRe-run with --apply to write.')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
