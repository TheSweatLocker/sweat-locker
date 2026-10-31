"""Retire the PASS/SKIP read receipts written before the rule existed.

== 2026-10-31 . R121/R122 ================================================
receipt_invariants rule (5) now refuses a tier=PASS or tier=SKIP receipt on
a read surface: a declared no-play must not publish as a priced, graded
bet. This clears the rows already written under the old behaviour.

ONLY UNGRADED ROWS. A settled result is history, and 12 PASS reads have
already graded (against COVERAGE 92, LEAN 33, STRONG 25). Rewriting those
changes what the record says about days that are closed, which is Andy's
call and not a side effect of shipping a rule. This script refuses any row
that carries a result, and says so per row.

WHY IT MATTERS TODAY. Two 10-31 NCAAF games published BOTH sides:

    Mississippi State @ Texas   fade 'Texas -20.5'    (public 70/68 on the dog)
                                read 'Mississippi State +20.5' tier=PASS conv=40
    Minnesota @ Indiana         fade 'Indiana -20.5'  (public 69/65 on the dog)
                                read 'Minnesota +20.5'         tier=PASS conv=40

The fade is the validated side (heavy public football spreads 58.9%, n=107,
ROI +12.4%); the read is an explicit no-play on the public side, at a
conviction that is an anti-signal on this sport (NCAAF top decile 36.67%,
ROI -33.76%, n=30). Both were set to grade. One had to lose.

THE READ ITSELF IS NOT REMOVED. GameDetailV2 renders a read from the sport
context's primary_play, not from public_receipts, and GameDetailV2.tsx:1060
already draws a PASS dim and low-conviction. The read stays exactly as
written; it just stops being scored as a bet nobody was told to make.

Confirms every write by READBACK.

USAGE
  python void_no_play_read_receipts.py --days 40
  python void_no_play_read_receipts.py --days 40 --apply
"""
from __future__ import annotations

import argparse
import collections
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

NO_PLAY = ('PASS', 'SKIP')
VOIDS = ('VOID', 'VOIDED')
#: the same set receipt_invariants.PLAY_SURFACES holds; imported so the two
#: cannot drift, with an explicit failure rather than a guessed fallback
try:
    from receipt_invariants import PLAY_SURFACES
except Exception as _e:                                        # noqa: BLE001
    print('cannot import PLAY_SURFACES from receipt_invariants: %s' % _e)
    raise SystemExit(2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=40)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    today = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()
    since = (today - dt.timedelta(days=a.days)).isoformat()
    print('[%s] retiring tier=PASS/SKIP receipts on READ surfaces since %s'
          % ('APPLY' if a.apply else 'DRY RUN', since))

    r = requests.get(SB + '/rest/v1/public_receipts', headers=H_R, timeout=120,
                     params={'select': 'id,sport,surface,market,tier,'
                                       'pick_label,pick_odds,result,game_date,'
                                       'conviction',
                             'game_date': 'gte.' + since,
                             'tier': 'in.(%s)' % ','.join(NO_PLAY),
                             'limit': 4000})
    if r.status_code != 200:
        print('  ! HTTP %s %s -- CANNOT VERIFY, nothing written'
              % (r.status_code, r.text[:160]))
        return 1
    rows = r.json()
    reads = [x for x in rows if str(x.get('surface')) not in PLAY_SURFACES]
    plays = [x for x in rows if str(x.get('surface')) in PLAY_SURFACES]
    print('  %d PASS/SKIP receipt(s) in window: %d on read surfaces, '
          '%d on PLAY surfaces' % (len(rows), len(reads), len(plays)))
    if plays:
        # not this script's job, but it must not be silent about them
        by = collections.Counter(str(x.get('surface')) for x in plays)
        print('  ! PASS/SKIP on a PLAY surface, NOT touched here: %s'
              % dict(by))

    graded = [x for x in reads if x.get('result')
              and str(x.get('result')).upper() not in VOIDS]
    already = [x for x in reads if str(x.get('result') or '').upper() in VOIDS]
    todo = [x for x in reads if not x.get('result')]
    print('  already settled (LEFT ALONE, history): %d' % len(graded))
    print('  already void: %d' % len(already))
    print('  ungraded, to retire: %d' % len(todo))
    if graded:
        bysport = collections.Counter(
            '%s/%s' % (x.get('sport'), str(x.get('result')).upper())
            for x in graded)
        print('      settled breakdown: %s' % dict(bysport))

    failed = done = 0
    for x in sorted(todo, key=lambda z: (str(z.get('game_date')),
                                         str(z.get('sport')))):
        print('  %s %s %-6s %-11s %-30s tier=%-5s odds=%-7s conv=%s'
              % (x['id'], x['game_date'], x['sport'], x['surface'],
                 str(x['pick_label'])[:30], x['tier'], x['pick_odds'],
                 x.get('conviction')))
        if not a.apply:
            continue
        pv = requests.patch(SB + '/rest/v1/public_receipts', headers=H_W,
                            timeout=60, params={'id': 'eq.%s' % x['id']},
                            json={'result': 'VOID',
                                  'graded_at': dt.datetime.now(
                                      dt.timezone.utc).isoformat()})
        back = pv.json() if pv.status_code in (200, 201) else []
        ok = bool(back) and str(back[0].get('result')) == 'VOID'
        if not ok:                        # readback, never the status code
            gg = requests.get(SB + '/rest/v1/public_receipts', headers=H_R,
                              timeout=60, params={'id': 'eq.%s' % x['id'],
                                                  'select': 'result'})
            gj = gg.json() if gg.status_code == 200 else []
            ok = bool(gj) and str(gj[0].get('result')) == 'VOID'
        if ok:
            done += 1
        else:
            failed += 1
            print('       ! HTTP %s NOT CONFIRMED' % pv.status_code)
    if a.apply:
        print('  retired %d, failed %d' % (done, failed))
    else:
        print('\nRe-run with --apply to write.')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
