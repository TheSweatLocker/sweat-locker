"""Void a card receipt whose BET is already in the record under another label.

== 2026-10-31 . ONE RESULT MUST MOVE THE RECORD ONCE ====================
The Sweat Card's POTD and its composite are separate components, and on
10-31 both landed on Marshall. The card itself is deduped now
(generate_sweat_card R119, dedupe_card_same_bet_twice), but two receipts
were already written:

    3982125  sweat_card  potd  'Marshall ML (Jerry 81/100)'  -145  PRIME
    3982894  sweat_card  ml    'Marshall ML'                 -142  STRONG

Marshall WON. Both read WIN, so the card record counts one result twice.

WHY THIS NEEDS ITS OWN PATH. dedupe_card_same_bet_twice refuses to void a
receipt that already carries a decided result -- correctly, because a
graded row is not something a dedupe pass should silently rewrite. But a
duplicate WIN inflates the record exactly as much as a duplicate loss
deflates it, and three of today's earlier duplicates were also all WINs.
So this is deliberate, narrow, and identity-gated rather than a loosening
of that rule.

THE GATE. A row is voided only when ALL of these hold:
  * it is on a card surface and is capture_mode='reconstructed'
  * a DIFFERENT receipt on the SAME surface and date carries the same bet
    (same sport, same market family, same team at the head of the label)
  * that survivor is NOT void and carries the SAME result
  * the survivor outranks it on tier
If the results disagree, nothing is written: that is a grading conflict,
not a duplicate, and it needs a human (the 10-13 lesson -- gate on
identity first, or a frozen receipt against a mutable source produces
hundreds of false conflicts).

Nothing leaves the record: the bet survives at the higher-tier receipt.

USAGE
  python void_card_duplicate_bet_receipts.py --date 2026-10-31
  python void_card_duplicate_bet_receipts.py --date 2026-10-31 --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
import re
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

CARD_SURFACES = ('sweat_card',)
VOIDS = ('VOID', 'VOIDED')
TIER_RANK = {'PRIME': 5, 'STRONG': 4, 'LEAN': 3, 'LIGHT': 2, 'COVERAGE': 1,
             'PASS': 0, 'SKIP': 0}


def get(path, **params):
    r = requests.get(SB + '/rest/v1/' + path, headers=H_R, timeout=90,
                     params=params)
    if r.status_code != 200:
        print('  ! %s HTTP %s %s' % (path, r.status_code, r.text[:140]))
        return None
    return r.json()


def mkt(v):
    m = str(v or '').lower()
    return {'potd': 'ml', 'moneyline': 'ml', 'spread': 'rl'}.get(m, m)


def team(lbl):
    s = ' '.join(str(lbl or '').split())
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)
    s = re.sub(r'\s+(ML|[+-]?\d+(?:\.\d+)?)$', '', s, flags=re.I)
    return s.strip().lower()


def res(v):
    return str(v or '').upper()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    date = a.date or (dt.datetime.now(dt.timezone.utc)
                      - dt.timedelta(hours=4)).date().isoformat()
    print('[%s] one bet, one receipt -- %s'
          % ('APPLY' if a.apply else 'DRY RUN', date))

    failed = done = 0
    for surface in CARD_SURFACES:
        rows = get('public_receipts',
                   select='id,sport,surface,market,tier,pick_label,pick_odds,'
                          'result,capture_mode,game_id',
                   game_date='eq.' + date, surface='eq.' + surface,
                   limit=500)
        if rows is None:
            return 1
        live = [x for x in rows if res(x.get('result')) not in VOIDS]
        groups = collections.defaultdict(list)
        for x in live:
            t = team(x.get('pick_label'))
            m = mkt(x.get('market'))
            if m in ('ml', 'rl') and t and t not in ('over', 'under'):
                groups[(str(x.get('sport')), m, t)].append(x)
        for key, grp in sorted(groups.items()):
            if len(grp) < 2:
                continue
            gids = {str(x.get('game_id')) for x in grp if x.get('game_id')}
            if len(gids) > 1:
                print('  %s: %d receipts across %d game_ids %s -- left '
                      'alone, doubleheader not duplicate'
                      % (key[2], len(grp), len(gids), sorted(gids)))
                continue
            results = {res(x.get('result')) for x in grp}
            if len(results) > 1:
                print('  %s: %d receipts DISAGREE on the result %s -- NOT '
                      'voided, that is a grading conflict for a human'
                      % (key[2], len(grp), sorted(results)))
                failed += 1
                continue
            grp = sorted(grp, key=lambda x: TIER_RANK.get(
                str(x.get('tier') or '').upper(), 0), reverse=True)
            keep, rest = grp[0], grp[1:]
            print('\n  %s %s %s: %d receipts for one bet'
                  % (key[0], key[1], key[2], len(grp)))
            print('     KEEP %s %-32r tier=%-8s odds=%-7s result=%r mode=%s'
                  % (keep['id'], keep['pick_label'], keep['tier'],
                     keep['pick_odds'], keep['result'], keep['capture_mode']))
            for r in rest:
                if str(r.get('capture_mode')) != 'reconstructed':
                    print('     SKIP %s %-32r is capture_mode=%r, not '
                          'reconstructed -- a LIVE receipt is a published '
                          'claim and is not voided here'
                          % (r['id'], r['pick_label'], r['capture_mode']))
                    continue
                print('     VOID %s %-32r tier=%-8s odds=%-7s result=%r'
                      % (r['id'], r['pick_label'], r['tier'], r['pick_odds'],
                         r['result']))
                if not a.apply:
                    continue
                pv = requests.patch(SB + '/rest/v1/public_receipts',
                                    headers=H_W, timeout=60,
                                    params={'id': 'eq.%s' % r['id']},
                                    json={'result': 'VOID',
                                          'graded_at': dt.datetime.now(
                                              dt.timezone.utc).isoformat()})
                back = pv.json() if pv.status_code in (200, 201) else []
                ok = bool(back) and str(back[0].get('result')) == 'VOID'
                if not ok:
                    gg = get('public_receipts', select='result',
                             id='eq.%s' % r['id']) or []
                    ok = bool(gg) and str(gg[0].get('result')) == 'VOID'
                print('          HTTP %s %s'
                      % (pv.status_code,
                         'CONFIRMED' if ok else 'NOT CONFIRMED'))
                done += 1 if ok else 0
                failed += 0 if ok else 1
    if a.apply:
        print('\n  voided %d, failed %d' % (done, failed))
    else:
        print('\nRe-run with --apply to write.')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
