#!/usr/bin/env python3
"""Void game_read receipts whose read has since been realigned to the OTHER side.

== WHY (2026-11-16) =========================================================
Andy: "fix all isues, optimize and ensure app si good to go for the day
across all surfaces".

assert_publish_invariant reported a CRITICAL on tonight's NBA slate:

    Portland Trail Blazers @ Minnesota Timberwolves [SIDE]
      4 surface(s) say 'Portland Trail Blazers ML' (AWAY/ml)
        [badge, card:unified_top_picks, read, receipt:sweat_card]
      BUT receipt:game_read says 'Minnesota Timberwolves ML' (HOME/ml)

A CRITICAL means a user can see one side while the record grades another.

THE SEQUENCE, from the stored audit trail:
  1. ensemble_v2 scored the game and had HOME (Minnesota) -- projected_spread
     -4.8, projected_home_wp 0.665.
  2. jerry_reads wrote 'Minnesota Timberwolves ML' and public_receipt froze
     that claim as a game_read receipt at 2026-11-15T22:52:16.
  3. primary_play then took an OC-DISSENT FLIP to AWAY (Portland): money% 60
     on AWAY against our HOME. The money data behind it is live and fresh
     (`pulled_at` 2026-11-16T11:45:16, source scoresandodds), so the flip is
     not built on the dead oddscrowd feed.
  4. The sweat_card receipt captured the FLIPPED side, Portland ML @ +119.
  5. backfill_jerry_pick_alignment (which nba_pipeline runs anyway) then
     realigned the read itself to Portland.

So the game_read receipt is the only surface still carrying the pre-flip
side. It is an honest record of what the read said at 22:52 -- and it is no
longer what any surface shows, so left alone it grades a side no user can
see. pick_side and pick_line are immutable by design (B181), so a
wrong-side receipt can only ever be VOIDED, never patched.
[[feedback_snapshot_before_the_mutation]] -- the receipt was taken before
the mutation it needed to see. [[feedback_two_writers_two_words_for_a_flip]]

WHAT THIS COSTS, STATED PLAINLY: the game_read record will not count this
game at all. That is the intended outcome -- Void is excluded from every
record by design, and counting the wrong side is worse than counting
nothing. A replacement game_read receipt is deliberately NOT written: its
published_at would be now rather than at publish time, which would claim
the read said Portland at 22:52 when it did not.
[[feedback_a_lookup_miss_became_a_settled_void]] is the opposite case and
does not apply -- this is a withdrawn claim, not a failed lookup.

NOT A FIX FOR THE CAUSE. The cause is that the publish gate ran BEFORE the
flip and was never re-evaluated after it: the same primary_play carries
`_mc_dissent` "MC hard dissent: sim has our side at 37.3% (<40% blocks
publish)" and `_edge_cap` "stored edge -5.33pp is not positive", and
generate_sharp_card's own coherence gate REFUSED this pick outright ("pick
is AWAY but our projected_spread favours HOME by 4.8"). The Sweat Card
published what the Sharp refused. Logged for Andy; not changed here.

SAFETY. Verifies per receipt that the CURRENT read disagrees with the
receipt's side before touching anything, refuses if the receipt is already
graded, and confirms every write by READBACK rather than the status code
([[feedback_204_is_not_a_write]]).

    python void_superseded_read_receipts.py                  # dry
    python void_superseded_read_receipts.py --apply
    python void_superseded_read_receipts.py --date 2026-11-16 --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv('.env')
load_dotenv('../.env')

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

GRADE = 'VOID'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    if not (SB and KEY):
        print('SUPABASE env missing')
        return 2

    r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R, timeout=60,
                     params={'select': 'id,surface,game_date,game_id,pick_label,'
                                       'pick_side,market,pick_odds,result',
                             'surface': 'eq.game_read',
                             'game_date': f'eq.{a.date}',
                             'limit': '1000'})
    if r.status_code != 200:
        print(f'receipt read failed {r.status_code}: {r.text[:200]}')
        return 2
    receipts = r.json()
    if len(receipts) >= 1000:
        print('  !! 1000-row cap hit - refusing to work from a truncated read')
        return 2
    print(f'=== superseded game_read receipts · {a.date} · '
          f'{"APPLY" if a.apply else "DRY"} ===')
    print(f'  game_read receipts on {a.date}: {len(receipts)}')

    voided = failed = 0
    for rc in receipts:
        if str(rc.get('result') or '').upper() in ('VOID', 'VOIDED'):
            continue
        if rc.get('result') is not None:
            continue          # already graded: not ours to withdraw
        gid = str(rc.get('game_id') or '')
        if not gid:
            continue
        q = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_R, timeout=60,
                         params={'select': 'id,call_side,call_market,call_text',
                                 'game_id': f'eq.{gid}',
                                 'game_date': f'eq.{a.date}'})
        reads = q.json() if q.status_code == 200 else []
        if not reads:
            continue
        rd = reads[0]
        rc_side = str(rc.get('pick_side') or '').upper()
        rd_side = str(rd.get('call_side') or '').upper()
        if not rc_side or not rd_side or rc_side == rd_side:
            continue
        # The read moved to the other side. This receipt is superseded.
        print(f'\n  receipt {rc["id"]}  {rc["pick_label"]!r} '
              f'({rc_side}/{rc.get("market")}) odds={rc.get("pick_odds")}')
        print(f'    read {rd["id"]} now says {rd.get("call_text")!r} '
              f'({rd_side}/{rd.get("call_market")}) -> receipt is SUPERSEDED')
        if not a.apply:
            continue
        pr = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                            timeout=60, params={'id': f'eq.{rc["id"]}'},
                            json={'result': GRADE,
                                  'graded_at': dt.datetime.now(
                                      dt.timezone.utc).isoformat()})
        back = pr.json() if pr.status_code in (200, 201) else []
        ok = bool(back) and str(back[0].get('result')) == GRADE
        if not ok:
            gg = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R,
                              timeout=60, params={'id': f'eq.{rc["id"]}',
                                                  'select': 'result'})
            gj = gg.json() if gg.status_code == 200 else []
            ok = bool(gj) and str(gj[0].get('result')) == GRADE
        print(f'    HTTP {pr.status_code} '
              f'{"CONFIRMED VOID" if ok else "NOT CONFIRMED - nothing changed"}')
        voided += 1 if ok else 0
        failed += 0 if ok else 1

    print(f'\n  voided={voided} failed={failed}')
    if not a.apply:
        print('  DRY RUN - re-run with --apply to write.')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
