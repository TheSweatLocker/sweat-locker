"""One bet must not occupy two slots on the home card.

== 2026-10-31 . MARSHALL IS ON THE SWEAT CARD TWICE ======================
ANDY: "is eveything that us rfacing to user correct and we stand ebhine
right now thats the imporant part right onw"

jerry_cache.sweat_card_2026-10-31 top_8 renders:

    1. Marshall ML (Jerry 81/100)   odds=-145  tier=PRIME   type=POTD
    4. Marshall ML                  odds=-142  tier=STRONG  type=ml

Same team, same market, same game (ncaaf_20261031_Marshall_Old Dominion).
The Sweat Card is "yesterday recap + POTD + Dawg + composite, top-8
curated", so the POTD and the composite are two different components -- and
when the composite independently lands on the POTD's game, one bet takes
two of the eight slots. A user reads that as two plays. Worse, both have
their own receipt:

    3982125  sweat_card  potd  PRIME   -145
    3982126  sweat_card  ml    STRONG  -142

so a single Marshall result moves the card record by TWO, in whichever
direction it lands. That is the same double-count class as the duplicate
receipts in repair_reconciled_card_item_identity, reached by a different
route: there the dedupe key was the wrong SHAPE, here there is no dedupe
between card components at all.

WHAT IT KEEPS. The POTD. It is the higher tier (PRIME over STRONG), it is
the flagship labelled play, and it has its own surface and its own
receipt lineage. The composite copy is the redundant one.

The play is NOT withdrawn: Marshall stays on the card, stays on The Sharp
(3982398, a separate surface with its own denominator), and stays in the
record once, at the POTD's price. Only the second copy of the same bet
goes.

KEYED ON game_id, NOT ON THE LABEL. "Marshall ML (Jerry 81/100)" and
"Marshall ML" are different strings, which is exactly why every
label-based duplicate check in this repo walked straight past this
(feedback_dedupe_key_shape_must_match). The anchoring pass in
repair_reconciled_card_item_identity put game_id back on these items,
which is what makes this check possible at all.

A label-keyed check would also have been WRONG in the other direction: on
10-03 two different games both carry the label 'under 7.0', with distinct
source_id hashes. Same text, different bets. game_id separates them.

USAGE
  python dedupe_card_same_bet_twice.py --date 2026-10-31
  python dedupe_card_same_bet_twice.py --date 2026-10-31 --apply
  python dedupe_card_same_bet_twice.py --days 30
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
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

BLOBS = ('top_8', 'unified_top_picks')
VOIDS = ('VOID', 'VOIDED')
#: higher wins the slot
TIER_RANK = {'PRIME': 5, 'STRONG': 4, 'LEAN': 3, 'COVERAGE': 2, 'PASS': 1}


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                      # noqa: BLE001
            return None
    return v if isinstance(v, (dict, list)) else None


def get(path, **params):
    r = requests.get(SB + '/rest/v1/' + path, headers=H_R, timeout=90,
                     params=params)
    if r.status_code != 200:
        print('  ! %s HTTP %s %s' % (path, r.status_code, r.text[:140]))
        return None
    return r.json()


def market(v):
    m = str(v or '').lower()
    return {'potd': 'ml', 'moneyline': 'ml', 'spread': 'rl'}.get(m, m)


def side(lbl):
    """The team/side at the head of the label, parenthetical stripped."""
    s = ' '.join(str(lbl or '').split())
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)
    s = re.sub(r'\s+(ML|[+-]?\d+(\.\d+)?)$', '', s)
    return s.strip().lower()


def rank(it):
    return TIER_RANK.get(str(it.get('tier') or '').upper(), 0)


def run_date(date: str, apply: bool) -> int:
    key = 'sweat_card_%s' % date
    rows = get('jerry_cache', select='data', cache_key='eq.' + key)
    if rows is None:
        return 1
    if not rows:
        return 0
    blob = jd(rows[0].get('data'))
    if not isinstance(blob, dict):
        return 0

    dropped_labels = []
    changed = False
    for name in BLOBS:
        items = blob.get(name)
        if not isinstance(items, list):
            continue
        groups = collections.defaultdict(list)
        for it in items:
            if not isinstance(it, dict):
                continue
            mk = market(it.get('type'))
            sd = side(it.get('pick') or it.get('label'))
            gid = it.get('game_id')
            # == WHY THE SIDE AND NOT THE game_id ========================
            # The POTD item and its receipt both carry game_id=None, so a
            # game_id-only key puts the POTD in its own group and walks
            # past the exact collision this script exists for. Within ONE
            # date, ONE sport and ONE market, a team is a team: 'Marshall
            # ML (Jerry 81/100)' and 'Marshall ML' are the same bet.
            #
            # Restricted to ml/rl with a team-shaped side, because on a
            # TOTAL the side is 'over'/'under', which two different games
            # share -- 10-03 carries two 'under 7.0' with distinct
            # source_id hashes: same text, different bets. Those keep the
            # game_id key, which separates them correctly.
            if mk in ('ml', 'rl') and sd and sd not in ('over', 'under'):
                groups[('SIDE', str(it.get('sport') or ''), mk, sd)].append(it)
            elif gid:
                groups[(str(gid), mk, sd)].append(it)
        drop = []
        for k, grp in groups.items():
            if len(grp) < 2:
                continue
            # DOUBLEHEADER GUARD. A team really can appear twice on one
            # date on the same market -- MLB plays two. If two copies BOTH
            # name a game_id and the ids differ, they are two bets, not
            # one, and this script must not touch them.
            gids = {str(x.get('game_id')) for x in grp if x.get('game_id')}
            if len(gids) > 1:
                print('  %s [%s] %s: %d copies across %d DIFFERENT game_ids '
                      '%s -- left alone, that is a doubleheader not a '
                      'duplicate' % (date, name, k[-1], len(grp), len(gids),
                                     sorted(gids)))
                continue
            grp.sort(key=rank, reverse=True)
            keep, rest = grp[0], grp[1:]
            print('  %s [%s] %s %s %s : %d copies of one bet'
                  % (date, name, k[0], k[1], k[2], len(grp)))
            print('      KEEP %r tier=%s odds=%s'
                  % (keep.get('pick') or keep.get('label'), keep.get('tier'),
                     keep.get('odds')))
            for r in rest:
                print('      DROP %r tier=%s odds=%s'
                      % (r.get('pick') or r.get('label'), r.get('tier'),
                         r.get('odds')))
                dropped_labels.append(r.get('pick') or r.get('label'))
            drop.extend(id(r) for r in rest)
        if drop:
            blob[name] = [it for it in items
                          if not (isinstance(it, dict) and id(it) in drop)]
            changed = True

    if not changed:
        return 0
    # the summary must be recomputed from what is left, or the card still
    # reports the double-count it no longer renders
    w = l = p = pend = 0
    for it in (blob.get('top_8') or []):
        if not isinstance(it, dict):
            continue
        r = str(it.get('result') or '').upper()
        if r in ('WIN', 'W', 'WON'):
            w += 1
        elif r in ('LOSS', 'L', 'LOST'):
            l += 1
        elif r in ('PUSH', 'TIE'):
            p += 1
        else:
            pend += 1
    blob['top_8_summary'] = {'wins': w, 'losses': l, 'pushes': p,
                             'pending': pend, 'resolved': w + l + p}
    blob['_same_bet_deduped_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    print('  %s top_8_summary -> %s' % (date, json.dumps(blob['top_8_summary'])))
    if not apply:
        return 0

    pr = requests.patch(SB + '/rest/v1/jerry_cache', headers=H_W, timeout=60,
                        params={'cache_key': 'eq.' + key},
                        json={'data': blob})
    ok = False
    back = pr.json() if pr.status_code in (200, 201) else []
    if back:
        b = jd(back[0].get('data')) or {}
        ok = (b.get('top_8_summary') == blob['top_8_summary']
              and len(b.get('top_8') or []) == len(blob.get('top_8') or []))
    print('  %s HTTP %s %s' % (date, pr.status_code,
                               'CONFIRMED' if ok else 'NOT CONFIRMED'))
    if not ok:
        return 1

    # the dropped copy has its own receipt and would still grade. Void it,
    # identity-gated on the label we actually dropped.
    for lbl in dropped_labels:
        rc = get('public_receipts',
                 select='id,pick_label,market,tier,pick_odds,result,surface',
                 game_date='eq.' + date, surface='eq.sweat_card',
                 pick_label='eq.' + str(lbl)) or []
        for x in rc:
            if str(x.get('result') or '').upper() in VOIDS:
                continue
            if x.get('result'):
                print('      receipt %s %r already graded %r -- LEFT ALONE, '
                      'a decided result is not mine to erase'
                      % (x['id'], lbl, x['result']))
                continue
            pv = requests.patch(SB + '/rest/v1/public_receipts', headers=H_W,
                                timeout=60, params={'id': 'eq.%s' % x['id']},
                                json={'result': 'VOID',
                                      'graded_at': dt.datetime.now(
                                          dt.timezone.utc).isoformat()})
            bk = pv.json() if pv.status_code in (200, 201) else []
            vok = bool(bk) and str(bk[0].get('result')) == 'VOID'
            print('      receipt %s %r -> VOID  HTTP %s %s'
                  % (x['id'], lbl, pv.status_code,
                     'CONFIRMED' if vok else 'NOT CONFIRMED'))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--days', type=int, default=1)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    anchor = dt.date.fromisoformat(
        a.date or (dt.datetime.now(dt.timezone.utc)
                   - dt.timedelta(hours=4)).date().isoformat())
    print('[%s] one bet, one slot' % ('APPLY' if a.apply else 'DRY RUN'))
    bad = 0
    for i in range(a.days):
        bad += run_date((anchor - dt.timedelta(days=i)).isoformat(), a.apply)
    if not a.apply:
        print('\nRe-run with --apply to write.')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
