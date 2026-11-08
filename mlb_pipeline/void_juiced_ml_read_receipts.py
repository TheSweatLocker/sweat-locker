#!/usr/bin/env python3
"""Void READ receipts whose moneyline is past the repo's own juice ceiling.

== 2026-11-07 . ONE CEILING, APPLIED EVERYWHERE ==========================
`ML_JUICE_MAX = -300` is the ceiling this repo already enforces on the
Sweat Card, the Sharp and the LR override, and `receipt_invariants` rule
(1) tests it. But that rule only REFUSES on a PLAY_SURFACE, and
`game_read` is deliberately not one -- a read is an opinion, not a play.
So the one surface where nothing stopped it accumulated the rows:

    113 ML receipts past -300, 110 of them on game_read
    NCAAB 68, NCAAF 24, NBA 11, NHL 3, MLB 2, NFL 2
    Oklahoma ML lean -100000 . Virginia ML lean -100000
    Kansas ML lean -7250 . Indiana ML lean -55000

WHY IT MATTERS, STATED HONESTLY. It is not an ROI hole. Graded, the set is
34W-4L, n=38, which is +0.07 units at a flat stake -- ROI +0.2%, against
an average break-even of 89.2% and an actual hit rate of 89.5%. It is
break-even, not a loser.

The damage is to the DISPLAYED record. 89.5% sits next to the game_read ML
book's own 61.3% (537-339, n=876) and reads as the best thing we do, while
returning nothing, because a -20000 favourite winning is not a result --
it is arithmetic. This is exactly
feedback_flat_110_inflates_every_ml_figure: a hit rate quoted without its
price is not a hit rate. Removing them moves the book to 60.0%
(503-335, n=838), which is worse-looking and true.

THE GATE. A row is voided only when ALL of these hold:
  * surface is NOT a PLAY_SURFACE (so a real published card play is never
    touched -- see the three below)
  * market is 'ml'
  * pick_odds is past ML_JUICE_MAX, imported rather than restated
  * result is not already VOID or NO_ACTION

SETTLED ROWS NEED THE FLAG. void_no_play_read_receipts says it best: a
settled result is history, and rewriting it "is Andy's call and not a side
effect of shipping a rule". So graded rows move only under
--include-graded, and the record delta is printed before anything is
written. Andy gave that call on 11-07.

NOT TOUCHED, ON PURPOSE. Three PLAY-surface receipts also sit past the
ceiling and are left alone, because each was genuinely published at that
price and a real published result is not ours to rewrite:

    24713    2026-05-27  MLB  sweat_card  Los Angeles Dodgers ML  -424  WIN
    3982129  2026-10-31  NHL  sweat_card  Colorado Avalanche ML   -385  VOID
    3982895  2026-10-31  NHL  sweat_card  Colorado Avalanche ML   -385  VOID

Recurrence is already gated upstream: price_picks demotes an unstarted read
whose ML prices past -300 to a pass, which is why the 11-07 board shows
`[ML JUICE]` on Edmonton -370 and 16 NCAAF reads. This script is for the
rows written before that existed.

Usage:
  python void_juiced_ml_read_receipts.py                     # dry run
  python void_juiced_ml_read_receipts.py --apply             # ungraded only
  python void_juiced_ml_read_receipts.py --apply --include-graded
"""
from __future__ import annotations

import argparse
import collections
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

SETTLED_ALREADY = ('VOID', 'VOIDED', 'NO_ACTION')

#: imported so the ceiling and the surface set cannot drift from the rule
#: that tests them, with an explicit failure rather than a guessed fallback
try:
    from receipt_invariants import ML_JUICE_MAX, PLAY_SURFACES
except Exception as _e:                                        # noqa: BLE001
    print('cannot import from receipt_invariants: %s' % _e)
    raise SystemExit(2)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _profit(odds: float, win: bool) -> float:
    """Units returned on a 1-unit stake."""
    if not win:
        return -1.0
    return odds / 100.0 if odds > 0 else 100.0 / (-odds)


def _page(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = '1000'
        p['offset'] = str(off)
        r = requests.get(SB + '/rest/v1/' + table, headers=H_R, params=p,
                         timeout=120)
        if r.status_code not in (200, 206):
            # A failed read must not look like an empty one. Choosing to
            # void nothing is correct; reporting zero rows is not.
            raise SystemExit('  ! HTTP %s %s -- CANNOT VERIFY, nothing '
                             'written' % (r.status_code, r.text[:200]))
        chunk = r.json()
        out.extend(chunk)
        if len(chunk) < 1000:
            break
        off += 1000
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--include-graded', action='store_true',
                    help='also void rows that already carry WIN/LOSS')
    a = ap.parse_args()
    mode = 'APPLY' if a.apply else 'DRY RUN'
    print('[%s] voiding READ-surface ML receipts past %g'
          % (mode, ML_JUICE_MAX))

    rows = _page('public_receipts',
                 {'select': 'id,sport,surface,market,game_date,pick_label,'
                            'pick_odds,result,tier,conviction',
                  'market': 'eq.ml'})
    cand, plays = [], []
    for x in rows:
        px = _f(x.get('pick_odds'))
        if px is None or px >= ML_JUICE_MAX:
            continue
        if str(x.get('result') or '').upper() in SETTLED_ALREADY:
            continue
        (plays if str(x.get('surface')) in PLAY_SURFACES else cand).append(x)

    print('  %d ML receipt(s) past the ceiling and not already retired: '
          '%d on read surfaces, %d on PLAY surfaces'
          % (len(cand) + len(plays), len(cand), len(plays)))
    if plays:
        # Not this script's job, but it must not be silent about them.
        print('  ! on a PLAY surface, NOT touched here (a real published '
              'price is not ours to rewrite):')
        for x in plays:
            print('      %s %s %-6s %-11s %-28s %+d result=%r'
                  % (x['id'], x['game_date'], x['sport'], x['surface'],
                     str(x.get('pick_label'))[:28], int(_f(x['pick_odds'])),
                     x.get('result')))

    graded = [x for x in cand
              if str(x.get('result') or '').upper() in ('WIN', 'LOSS')]
    ungraded = [x for x in cand if x not in graded]
    print('  read-surface: %d graded, %d ungraded'
          % (len(graded), len(ungraded)))

    if graded:
        w = sum(1 for x in graded if str(x['result']).upper() == 'WIN')
        u = sum(_profit(_f(x['pick_odds']),
                        str(x['result']).upper() == 'WIN') for x in graded)
        print('    graded set: %dW-%dL = %.1f%%, %+.2f units at a flat stake '
              '(ROI %+.1f%%)' % (w, len(graded) - w,
                                 100.0 * w / len(graded), u,
                                 100.0 * u / len(graded)))
        print('    by sport: %s'
              % dict(collections.Counter(x['sport'] for x in graded)))

    targets = cand if a.include_graded else ungraded
    if graded and not a.include_graded:
        print('    graded rows HELD -- pass --include-graded to retire them')
    print('  -> %d row(s) to void' % len(targets))
    for x in sorted(targets, key=lambda z: _f(z['pick_odds'])):
        print('      %s %s %-6s %-30s %+8d tier=%s result=%r'
              % (x['id'], x['game_date'], x['sport'],
                 str(x.get('pick_label'))[:30], int(_f(x['pick_odds'])),
                 x.get('tier'), x.get('result')))
    if not a.apply:
        print('DRY RUN -- re-run with --apply to write.')
        return 0

    done = failed = 0
    for x in targets:
        r = requests.patch(SB + '/rest/v1/public_receipts?id=eq.%s' % x['id'],
                           headers=H_W, timeout=30, json={'result': 'VOID'})
        if r.status_code not in (200, 204):
            print('      ! PATCH %s on %s: %s'
                  % (r.status_code, x['id'], r.text[:140]))
            failed += 1
            continue
        # A 204 is not a write, and this column has frozen before. Confirm.
        back = requests.get(SB + '/rest/v1/public_receipts', headers=H_R,
                            timeout=30,
                            params={'select': 'id,result',
                                    'id': 'eq.%s' % x['id']}).json() or []
        if not back or str(back[0].get('result') or '').upper() != 'VOID':
            print('      ! NOT confirmed on readback for %s (frozen column?)'
                  % x['id'])
            failed += 1
            continue
        done += 1
    print('  voided %d of %d (%d not confirmed)'
          % (done, len(targets), failed))
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
