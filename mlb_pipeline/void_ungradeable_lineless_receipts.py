#!/usr/bin/env python3
"""void_ungradeable_lineless_receipts - VOID grades the missing line decided.

== ANDY, 2026-11-04: "void." =============================================
B142 cleanup. 54 receipts across the whole table claim a line-bearing market
while carrying no line, every one capture_mode='reconstructed'. 23 were
already VOID (the same class was cleaned once before, which is the precedent
this follows rather than a new state). The rest are graded.

== WHY THIS DOES NOT VOID ALL 31 =========================================
A missing line is a display defect always, but a GRADING defect only
sometimes. For an NHL run line the number is conventionally +/-1.5, so for
many rows both readings - laying 1.5 as the favourite or taking 1.5 as the
dog - land on the same verdict, and the stored grade is then correct no
matter which was meant. Measured against nhl_game_results on all 23 graded
NHL rl rows:

    both readings give the SAME verdict   12   <- grade is SAFE, keep it
    readings DISAGREE                     11   <- the missing line decided it

Voiding those 12 would delete a correct grade to tidy a cosmetic gap. So the
target is only the rows where the absent number actually determines the
outcome: the 11 NHL rows plus the NBA rows, where 'Over' and
'Dallas Mavericks ATS' carry no conventional line at all and the verdict can
never be derived.

The test is run PER ROW here, not assumed from the sport, because the split
above is a property of the final margin and not of the league.

== WHY VOID, AND WHAT IT DOES TO THE RECORD ==============================
`result` is mutable under trg_freeze_receipt_identity; pick_line is not, so
these rows cannot be repaired in place (proven tonight - 2 of 129 price
backfill writes were refused on exactly that column). VOID is the only field
we are permitted to set, and compute_surface_records counts only
result in ('win','loss'), so a VOID row leaves W/L and every rate built on
it.

STATED PLAINLY BECAUSE IT CUTS OUR WAY: the rows being voided are mostly
LOSSES, so this IMPROVES the displayed record. That is not the reason for
doing it and it is not cherry-picking - the selection rule is "the missing
line decided the grade", applied before looking at whether the row said WIN
or LOSS, and it removes wins too. The exact W/L delta is printed before any
write so the change is never silent.

SAFETY
  * Targets only: market needs a line AND pick_line IS NULL AND
    recover_line() cannot fill it from the label AND result is WIN/LOSS AND
    the two line readings disagree.
  * Never touches a row whose grade is determinable.
  * Dry-run by default. --apply writes.
  * Every PATCH is read back, because a 204 is not a write.

CLI:
  python void_ungradeable_lineless_receipts.py
  python void_ungradeable_lineless_receipts.py --apply
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text().split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}
VOID = 'VOID'

_RESULTS = {'NHL': 'nhl_game_results', 'NBA': 'nba_game_results',
            'NCAAB': 'ncaab_game_results', 'NFL': 'nfl_game_results',
            'NCAAF': 'ncaaf_game_results', 'MLB': 'mlb_game_results'}
_LINE_MKT = {'spread', 'rl', 'total', 'prop'}


def _page(tbl, params, cap=40000):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = '1000'
        p['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_R, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  read {tbl} {r.status_code}: {r.text[:140]}')
            break
        b = r.json()
        if not b:
            break
        out += b
        off += 1000
        if off > cap:
            break
    return out


# The +/-1.5 handicap is a CONVENTION, and only in these sports. NHL posts a
# puck line and MLB a run line that are +/-1.5 essentially always, so a
# lineless row there can be tested against both readings. Basketball and
# football spreads are whatever the market says, so a lineless row in those
# sports can never be settled at all.
#
# I had this wrong on the first pass: applying the 1.5 test to NBA spared 4
# graded NBA rl rows (3 LOSS, 1 WIN, all 10-30) purely because a basketball
# margin is rarely inside +/-1.5 — exactly the kind of borrowed convention
# this repo keeps getting bitten by. The 14 OTHER NBA rl rows of this class
# are already VOID in the data, which is the precedent agreeing.
_CONVENTIONAL_1_5 = {'NHL', 'MLB'}


def _decides(rc, margin):
    """True when the ABSENT line changes the verdict for this row."""
    mk = str(rc.get('market') or '').lower()
    if mk in ('total', 'prop'):
        return True              # no number, no settleable outcome, ever
    if str(rc.get('sport') or '').upper() not in _CONVENTIONAL_1_5:
        return True              # no conventional handicap to fall back on
    side = str(rc.get('pick_side') or '').upper()
    if side.startswith('HOME'):
        got = margin
    elif side.startswith('AWAY'):
        got = -margin
    else:
        return True              # no side either: nothing to settle
    return (got > 1.5) != (got > -1.5)


def run(apply_it):
    rows = _page('public_receipts', {
        'select': 'id,sport,market,pick_label,pick_side,pick_line,result,'
                  'game_date,game_id,capture_mode'})
    try:
        from public_receipt import recover_line, unactionable
    except Exception as e:
        print(f'  cannot import the shared guards: {e}')
        return 1
    lineless = []
    for x in rows:
        if str(x.get('market') or '').lower() not in _LINE_MKT:
            continue
        if x.get('pick_line') is not None:
            continue
        y = dict(x)
        recover_line(y)
        if y.get('pick_line') is not None:
            continue             # the number was in the label all along
        if not unactionable(y):
            continue
        lineless.append(x)
    print(f'  lineless, unrecoverable receipts: {len(lineless)}')
    print(f'    by result: {dict(Counter(str(x.get("result")) for x in lineless))}')
    graded = [x for x in lineless
              if str(x.get('result') or '').upper() in ('WIN', 'LOSS')]
    print(f'    graded WIN/LOSS: {len(graded)}')
    # margins, per sport, only for the sports actually present
    margins = {}
    for sp in sorted({x['sport'] for x in graded}):
        tbl = _RESULTS.get(sp)
        if not tbl:
            continue
        for g in _page(tbl, {'select': 'game_id,home_score,away_score'}):
            if g.get('home_score') is None or g.get('away_score') is None:
                continue
            margins[(sp, g['game_id'])] = g['home_score'] - g['away_score']
    target, safe, unknown = [], [], []
    for x in graded:
        m = margins.get((x['sport'], x.get('game_id')))
        if m is None:
            unknown.append(x)
            continue
        (target if _decides(x, m) else safe).append(x)
    print(f'\n  the missing line DECIDED the grade : {len(target)}  <- void')
    print(f'  grade holds either way             : {len(safe)}  <- keep')
    print(f'  no result row to test against      : {len(unknown)}  <- keep')
    if safe:
        print('\n  KEEPING these — a correct grade is not deleted to tidy a '
              'cosmetic gap:')
        for x in safe[:6]:
            print(f'      id={x["id"]:<9} {x["sport"]:<5} {x["market"]:<6} '
                  f'{str(x.get("pick_label"))[:28]:<28} {x["result"]}')
        if len(safe) > 6:
            print(f'      ... and {len(safe) - 6} more')
    if not target:
        print('\n  nothing to void')
        return 0
    w = sum(1 for x in target if str(x['result']).upper() == 'WIN')
    l = len(target) - w
    print(f'\n  RECORD IMPACT of voiding {len(target)}: removes {w} WIN and '
          f'{l} LOSS')
    print(f'    this {"IMPROVES" if l > w else "WORSENS" if w > l else "does not move"} '
          f'the displayed record — stated up front, not buried')
    print(f'    by sport/market: '
          f'{dict(Counter((x["sport"], x["market"]) for x in target))}')
    print()
    for x in target:
        print(f'    id={x["id"]:<9} {x["sport"]:<5} {x["market"]:<6} '
              f'{str(x.get("pick_label"))[:30]:<30} {x["result"]:<5} '
              f'{x["game_date"]}')
    if not apply_it:
        print(f'\n  [DRY] would set result={VOID} on {len(target)} receipts. '
              f're-run with --apply')
        return 0
    ok = bad = 0
    for x in target:
        p = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                           params={'id': f'eq.{x["id"]}'},
                           data=json.dumps({'result': VOID}), timeout=30)
        v = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R,
                         params={'id': f'eq.{x["id"]}', 'select': 'id,result'},
                         timeout=20)
        got = (v.json() or [{}])[0].get('result') if v.status_code == 200 \
            else None
        if str(got).upper() == VOID:
            ok += 1
        else:
            bad += 1
            print(f'    ! id={x["id"]} NOT voided (patch {p.status_code}, '
                  f'reads back {got!r})')
    print(f'\n  VOID confirmed on readback: {ok} of {len(target)}')
    if bad:
        print(f'  failed: {bad} — a 204 is not a write, which is why every '
              f'row is read back')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print(f'=== void_ungradeable_lineless_receipts · '
          f'{"APPLY" if a.apply else "DRY"} ===')
    sys.exit(run(a.apply))


if __name__ == '__main__':
    main()
