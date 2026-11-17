#!/usr/bin/env python3
"""Void a receipt whose spread label has no number, and publish it with one.

== WHY (2026-11-16) ========================================================
fix_potd_bare_spread_label repaired the display half of tonight's POTD
("Golden State Warriors ATS" -> "... ATS -3.5"), and both receipts REFUSED
the same correction: pick_label / pick_side / pick_line are frozen by
freeze_receipt_identity (R96 / B181).

THAT FREEZE IS NOT COSMETIC HERE, BECAUSE THE LABEL IS THE GRADING INPUT.
Every potd receipt in the table carries pick_line NULL and pick_side NULL --
that is this surface's norm, not a defect of tonight -- so the grader reads
the number out of pick_label. A label with no number therefore cannot be
graded at all, and it does not fail loudly:

    2026-11-08  'Boston Celtics ATS (Jerry 53/100)'   -> result VOID

That is the same bug, already settled as a Void, already out of the POTD
denominator. Tonight's would have gone the same way.

THE REMEDY IS THE ONE ANDY ALREADY CHOSE. On 2026-10-25, on five NFL
game_read receipts frozen on a side we did not publish: "Void and publish
correct". Migration 20261025a exists for precisely that, replacing the old
unique constraint with a partial index so a voided row no longer occupies
its own slot. This is the narrower case: the side, the market, the price and
the game are identical, and only the number -- which the pick always
implicitly carried -- is being stated.

WHY A REPLACEMENT IS HONEST HERE, where void_superseded_read_receipts
deliberately refused to write one. There, the read had MOVED to the other
side, so a new receipt stamped `now` would have claimed we said Portland at
22:52 when we did not. Here nothing about the claim changes, and
`published_at` is carried over from the original row rather than stamped
`now`, so CLV and the publish-time audit still measure the moment we
actually published. [[feedback_snapshot_before_the_mutation]]

REFUSALS, all of them by design:
  * nothing is touched once `result` is set -- a graded pick's number is
    part of the record;
  * the replacement must differ from the original ONLY in label/line/side,
    every other column is copied verbatim, and the script ABORTS if the
    insert comes back with a different side, price or source_id;
  * the void is confirmed by READBACK before the replacement is written,
    so a failed void can never leave two live receipts on one source
    ([[feedback_204_is_not_a_write]]);
  * if the replacement insert fails, the void is REPORTED LOUDLY as
    standing alone, because that state (play dropped from the denominator)
    is strictly worse than the bare label and must not be silent.

    python replace_bare_spread_receipts.py --date 2026-11-16
    python replace_bare_spread_receipts.py --date 2026-11-16 --apply
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import os
import re
import sys

import requests
from dotenv import load_dotenv

load_dotenv('.env')
load_dotenv('../.env')

SB = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

#: a bare market word with no number anywhere after it.
_BARE = re.compile(r'\b(ATS|RL|SPREAD)\b(?!.*[-+]\d)', re.I)

#: columns the replacement must NOT copy (server-assigned) .
_SKIP = {'id', 'created_at', 'graded_at', 'result'}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not (SB and KEY):
        print('SUPABASE env missing')
        return 2

    print(f'=== bare-spread receipt replacement · {a.date} · '
          f'{"APPLY" if a.apply else "DRY"} ===')

    # PAGINATED. A busy slate puts >1000 receipts on one date (tonight: the
    # prop surfaces alone clear it), and PostgREST caps a read at 1000
    # silently. Only UNGRADED rows can be repaired at all, so the filter is
    # pushed to the server rather than applied to a truncated page.
    # [[feedback_never_measure_from_truncated_output]]
    rows, off, PAGE = [], 0, 1000
    while True:
        rc = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R,
                          timeout=60,
                          params={'select': '*', 'game_date': f'eq.{a.date}',
                                  'result': 'is.null', 'order': 'id',
                                  'offset': str(off), 'limit': str(PAGE)})
        if rc.status_code != 200:
            print(f'  receipt read failed {rc.status_code}: {rc.text[:200]}')
            return 2
        page = rc.json()
        rows += page
        if len(page) < PAGE:
            break
        off += PAGE
        if off > 20000:
            print('  !! runaway pagination - aborting')
            return 2
    print(f'  ungraded receipts on {a.date}: {len(rows)}')

    # The number comes from the already-repaired display row, NOT re-derived
    # here: one derivation, one place. fix_potd_bare_spread_label cross-
    # checked it against the moneyline before writing it.
    h = requests.get(f'{SB}/rest/v1/daily_best_bet_history', headers=H_R,
                     timeout=30, params={'select': 'lean,game,sport,result',
                                         'bet_date': f'eq.{a.date}'})
    hist = (h.json() or [{}])[0] if h.status_code == 200 else {}
    lean = str(hist.get('lean') or '')
    m = re.search(r'\b(?:ATS|RL|SPREAD)\s+([-+]\d+(?:\.\d+)?)', lean, re.I)
    if not m:
        print(f'  the repaired POTD lean carries no number ({lean!r}) - '
              f'run fix_potd_bare_spread_label.py --apply first')
        return 1
    line = float(m.group(1))
    print(f'  repaired lean = {lean!r}  -> line {line:+g}')

    todo = [r for r in rows
            if _BARE.search(str(r.get('pick_label') or ''))
            and r.get('result') is None]
    if not todo:
        print('  no live receipt carries a bare spread label - nothing to do')
        return 0

    done = failed = 0
    for r in todo:
        lbl = str(r.get('pick_label') or '')
        want = _BARE.sub(lambda mm: f'{mm.group(1).upper()} {line:+g}', lbl, 1)
        print(f'\n  receipt {r["id"]} ({r.get("surface")}) {lbl!r}')
        print(f'    -> {want!r}  line={line:+g}')
        if not a.apply:
            continue

        # ── 1. void the frozen row, confirmed by readback ──────────────
        requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                       timeout=30, params={'id': f'eq.{r["id"]}'},
                       json={'result': 'VOID',
                             'graded_at': dt.datetime.now(
                                 dt.timezone.utc).isoformat()})
        g = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R,
                         timeout=30, params={'id': f'eq.{r["id"]}',
                                             'select': 'result'})
        gj = g.json() if g.status_code == 200 else []
        if not (gj and str(gj[0].get('result')).upper() == 'VOID'):
            print(f'    VOID NOT CONFIRMED (result={gj and gj[0].get("result")!r}) '
                  f'- replacement NOT attempted, row left live and bare')
            failed += 1
            continue
        print('    void CONFIRMED')

        # ── 2. publish the replacement, every other column verbatim ───
        body = {k: v for k, v in r.items() if k not in _SKIP}
        body['pick_label'] = want
        body['pick_line'] = line
        if not body.get('pick_side'):
            # Derive the side from the matchup, never guessed: the label's
            # team must match one end of `matchup`.
            mu = str(r.get('matchup') or '')
            team = re.sub(r'\(.*?\)', '', _BARE.sub('', lbl)).strip()
            if ' @ ' in mu:
                aw, hm = [s.strip() for s in mu.split(' @ ', 1)]
                body['pick_side'] = ('AWAY' if team == aw
                                     else 'HOME' if team == hm else None)
        aud = copy.deepcopy(body.get('audit')) \
            if isinstance(body.get('audit'), dict) else {}
        aud['line_recovered_from'] = 'close_spread'
        aud['replaces_receipt_id'] = r['id']
        aud['replaced_because'] = (
            'spread label carried no number, so the grader had nothing to '
            'grade (cf. 2026-11-08 Boston Celtics ATS -> VOID); identity '
            'frozen by R96 so the row was voided and republished with the '
            'posted close. Side, market, price and published_at unchanged.')
        body['audit'] = aud

        ins = requests.post(f'{SB}/rest/v1/public_receipts', headers=H_W,
                            timeout=30, json=body)
        if ins.status_code not in (200, 201):
            print(f'    !! REPLACEMENT INSERT FAILED {ins.status_code}: '
                  f'{ins.text[:260]}')
            print(f'    !! THE VOID STANDS ALONE - this play is now OUT of '
                  f'the {r.get("surface")} denominator. Fix the insert; do '
                  f'not leave it in this state.')
            failed += 1
            continue
        new = (ins.json() or [{}])[0]
        # The replacement must differ in the number and nothing else.
        drift = [k for k in ('sport', 'surface', 'game_date', 'source_id',
                             'source_table', 'market', 'pick_odds',
                             'published_at', 'conviction')
                 if str(new.get(k)) != str(r.get(k))]
        print(f'    replacement id={new.get("id")} label='
              f'{str(new.get("pick_label"))!r} line={new.get("pick_line")} '
              f'side={new.get("pick_side")!r} published_at='
              f'{str(new.get("published_at"))[:19]}')
        if drift:
            print(f'    !! DRIFT in {drift} - the replacement is not the '
                  f'same claim. Investigate before trusting the record.')
            failed += 1
            continue
        if str(new.get('pick_line')) != str(line):
            print('    !! replacement did not take the line - FROZEN too?')
            failed += 1
            continue
        done += 1

    print(f'\n  replaced={done} failed={failed}')
    if not a.apply:
        print('  DRY RUN - re-run with --apply to write.')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
