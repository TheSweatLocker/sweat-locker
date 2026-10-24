#!/usr/bin/env python3
"""Give historical COHORT_* / ANCHOR rows the direction they never recorded.

══ WHY ══════════════════════════════════════════════════════════════════
931 of 2,034 signal_attribution rows (46%) were written with
signal_side='NEUTRAL' -- every COHORT_* row and every ANCHOR row. The cost is
not cosmetic. "Did the side this signal argued cover?" is unanswerable for
them, so the only number available was OUR PICK's result on games where the
signal fired. That measures our selection, not the signal.

ANCHOR's "46.2% on n=145" and COHORT_NCAAF_HEAVY_HOME_DOG's "40.6% on n=64"
were exactly that, and I used them to recommend retiring both signals.
Andy: "why retire automatically intesad of looking liek whats driving those
negative ROI". There was nothing to look at, which is the actual defect.

Nothing new has to be collected. The information is already in the rows:
  * a cohort tag names its SUBJECT  -- every side-bearing tag on the board is
    home-referenced (HOME_FAV, HEAVY_HOME_FAV, HEAVY_HOME_DOG,
    DIV_HOME_COVER). SHOOTOUT and GRINDER describe the TOTAL and name no
    side, so they stay NEUTRAL rather than get a fabricated one.
  * ANCHOR is a deference weight, so its subject is the side the MARKET
    favours -- which close_line already gives.

Recording the subject is deliberately not recording a recommendation. Whether
to follow or fade becomes a calibration OUTPUT rather than an assumption baked
into a writer (feedback_signal_needs_a_direction_control).

SAFETY. This is an attribution/diagnostics table, not public_receipts: no
pick, price, label or grade is touched, and nothing user-facing reads it.
Only rows currently reading 'NEUTRAL' are written, so a real recorded side can
never be overwritten, and the operation is reversible by setting them back.

The close_line orientation is MEASURED, not assumed. Tested against the stored
result on every graded spread row:
    NCAAF  home covers iff margin > -close_line   694/774 reproduce it
    NFL    home covers iff margin >  close_line   225/225 reproduce it
A sport without a measured convention is skipped, because guessing one is
indistinguishable from knowing it (feedback_edge_formula_sign_audit).

Usage:
    python backfill_signal_sides.py            # dry run
    python backfill_signal_sides.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        if '=' in _ln and not _ln.strip().startswith('#'):
            _k, _v = _ln.strip().split('=', 1)
            os.environ.setdefault(_k, _v)

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = dict(H, **{'Content-Type': 'application/json'})

#: Sign of close_line that means HOME IS FAVOURED, per sport. Measured.
CLOSE_LINE_HOME_FAV_SIGN = {'NCAAF': -1.0, 'NFL': 1.0}


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def subject_side(row: dict) -> str | None:
    """The side this signal is ABOUT, or None when it names none."""
    key = str(row.get('signal_key') or '').upper()
    if key.startswith('COHORT_'):
        return 'HOME' if 'HOME' in key else None
    if key == 'ANCHOR':
        sgn = CLOSE_LINE_HOME_FAV_SIGN.get(str(row.get('sport') or '').upper())
        if sgn is None:
            return None
        cl = f(row.get('close_line'))
        if cl is None or abs(cl) < 0.25:
            return None                       # a pick'em has no favourite
        return 'HOME' if sgn * cl > 0 else 'AWAY'
    return None


def page(params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/signal_attribution', headers=H,
                         timeout=30,
                         params={**params, 'offset': str(off),
                                 'limit': '1000', 'order': 'id.asc'})
        if r.status_code != 200:
            print(f'  ⚠ GET HTTP {r.status_code}: {r.text[:160]}')
            return out
        b = r.json() or []
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    rows = page({'select': 'id,sport,signal_key,signal_side,close_line',
                 'signal_side': 'eq.NEUTRAL'})
    print(f'=== backfill_signal_sides · '
          f'{"APPLY" if a.apply else "DRY RUN"} ===')
    print(f'  rows reading NEUTRAL: {len(rows)}')
    plan: dict[str, list[int]] = {'HOME': [], 'AWAY': []}
    skipped: dict[str, int] = {}
    for r in rows:
        side = subject_side(r)
        if side is None:
            k = str(r.get('signal_key'))
            skipped[k] = skipped.get(k, 0) + 1
            continue
        plan[side].append(int(r['id']))
    for side, ids in plan.items():
        print(f'  -> {side}: {len(ids)} row(s)')
    if skipped:
        print('  left NEUTRAL (names no side, correctly):')
        for k, n in sorted(skipped.items(), key=lambda z: -z[1]):
            print(f'       {k:36s} {n}')
    if not a.apply:
        print('  dry run — nothing written')
        return 0
    done = 0
    for side, ids in plan.items():
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            inlist = ','.join(str(x) for x in chunk)
            up = requests.patch(
                f'{SB}/rest/v1/signal_attribution', headers=HW, timeout=60,
                params={'id': f'in.({inlist})',
                        'signal_side': 'eq.NEUTRAL'},
                data=json.dumps({'signal_side': side}))
            if up.status_code not in (200, 204):
                print(f'  ⚠ PATCH HTTP {up.status_code}: {up.text[:200]}')
                continue
            done += len(chunk)
    # A 204 is not a write (feedback_204_is_not_a_write): count what MOVED.
    left = page({'select': 'id', 'signal_side': 'eq.NEUTRAL'})
    print(f'  patched {done} row(s); still NEUTRAL: {len(left)} '
          f'(expected {sum(skipped.values())})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
