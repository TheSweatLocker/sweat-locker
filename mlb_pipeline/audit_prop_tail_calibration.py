"""Does our edge claim hold in the PLUS-MONEY tail, or do we over-project?

ANDY 2026-10-12, choosing cause over filter: "Leave it, investigate the
cause first."

THE CELL THIS EXPLAINS. On clean receipts since 9/22, PRIME on plus money
is 11-26 (29.7%) against a 47.4% breakeven — ROI -35.9%, CI[-65.9,-2.1].
Plus money is NOT bad in other tiers (LEAN +4.1%, COVERAGE +24.5%,
untiered +4.2% on n=466), so this is not "dogs lose". And the 37 bets are
not underdog teams at all: 34 are MLB and 30 are pitcher counting props
(ks / outs / hits-allowed / earned-runs) clustered at +100..+140.

THE STRUCTURAL REASON TO SUSPECT THE TAIL. For an OVER to pay PLUS money
the book has set the line ABOVE its own expectation. For us to then call it
PRIME, our number must sit well above an already-high line. That is the
right tail of a counting-stat projection — exactly where such models break,
because the mean is fit on the body of the distribution.

WHAT IS MEASURED. mlb_pipeline_props stores no projection, but it stores
`final_value`, which is better for this: it lets the REALIZED margin be
measured directly rather than inferred.

    signed_margin = (final_value - prop_line) for an OVER
                    (prop_line - final_value) for an UNDER

Positive means the result went our way. The test is whether the mean signed
margin decays as the price moves from favourite to plus money. If it does,
the line we are crossing is further from reality the further out we go, and
the fix belongs in the projection's tail, not in a tier filter.

CONTROLS, because a single decaying column proves little:
  * the same breakdown for NON-plus-money bands, so "the tail is worse" has
    something to be worse THAN;
  * conviction banded inside the plus-money region — if conviction carries
    real information there, higher conviction should mean a better margin.
    If it is flat or inverted, conviction itself is what fails in the tail;
  * OVER and UNDER split, because an UNDER at plus money is the mirror
    situation (line set BELOW expectation) and should fail the same way if
    the cause is tail projection rather than something over-specific.

WRITES NOTHING.

CLI
    python audit_prop_tail_calibration.py
    python audit_prop_tail_calibration.py --since 2026-08-01
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML

#: Pitcher counting props — the families the PRIME plus-money cell is made of.
PITCHER = ('ks_', 'outs_', 'ha_', 'er_', 'pitching_')

BANDS = [(-100000, -200, 'heavy fav <= -200'),
         (-200, -150, 'mod fav  -200..-150'),
         (-150, -120, 'light fav-150..-120'),
         (-120, -100, 'near pick-120..-100'),
         (-100, 100000, 'PLUS MONEY >= +100')]


def page(table, params, cap=400000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=180)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                                  # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def boot_ci(vals, iters=6000, seed=29):
    rng = random.Random(seed)
    n = len(vals)
    out = []
    for _ in range(iters):
        t = 0.0
        for _i in range(n):
            t += vals[rng.randrange(n)]
        out.append(t / n)
    out.sort()
    return out[int(iters * 0.025)], out[int(iters * 0.975)]


def row(label, rows, pad=22, unit='units of the stat'):
    n = len(rows)
    if n < 20:
        print(f'    {label:<{pad}} n={n} too thin')
        return
    m = [r['margin'] for r in rows]
    mean = statistics.fmean(m)
    lo, hi = boot_ci(m)
    hit = sum(1 for r in rows if r['margin'] > 0) / n * 100
    clears = not (lo <= 0 <= hi)
    print(f'    {label:<{pad}} margin {mean:+6.2f}  CI[{lo:+6.2f},{hi:+6.2f}]'
          f'{" *" if clears else "  "}  went our way {hit:5.1f}%  n={n}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-08-01')
    a = ap.parse_args()

    props = page('mlb_pipeline_props', {
        'select': 'id,game_date,player_name,prop_type,prop_line,direction,'
                  'final_value,conviction,tier,result',
        'game_date': f'gte.{a.since}'})
    rec = page('public_receipts', {
        'select': 'game_date,player_name,prop_type,pick_line,pick_odds,tier,'
                  'result,market,sport',
        'sport': 'eq.MLB', 'game_date': f'gte.{a.since}'})

    # price comes from the receipt; the realized value from the props row
    px = {}
    for x in rec:
        if str(x.get('market')) != 'prop':
            continue
        o = f(x.get('pick_odds'))
        if o is None or o == 0 or abs(o) > 2000:
            continue
        k = (str(x.get('game_date'))[:10],
             str(x.get('player_name')).strip().lower(),
             str(x.get('prop_type')), str(f(x.get('pick_line'))))
        px[k] = o

    rows = []
    for p in props:
        fv, pl = f(p.get('final_value')), f(p.get('prop_line'))
        if fv is None or pl is None:
            continue
        d = str(p.get('direction') or '').lower()
        if d not in ('over', 'under'):
            continue
        k = (str(p.get('game_date'))[:10],
             str(p.get('player_name')).strip().lower(),
             str(p.get('prop_type')), str(pl))
        o = px.get(k)
        if o is None:
            continue
        margin = (fv - pl) if d == 'over' else (pl - fv)
        rows.append({'margin': margin, 'odds': o, 'dir': d,
                     'conv': f(p.get('conviction')) or 0.0,
                     'tier': str(p.get('tier')),
                     'ptype': str(p.get('prop_type')),
                     'pitcher': str(p.get('prop_type') or '').startswith(PITCHER)})

    print(f'=== prop tail calibration · MLB · since {a.since} ===')
    print(f'    {len(props)} prop rows · {len(rows)} joined to a real price')
    print('    margin = how far the result landed PAST the line, our way.')
    print('    A negative mean means the line we crossed was too far out.\n')
    if len(rows) < 60:
        print('  not enough joined rows to judge')
        return 0

    pit = [r for r in rows if r['pitcher']]
    print(f'-- ALL MLB props by price band (n={len(rows)})')
    for lo, hi, lbl in BANDS:
        row(lbl, [r for r in rows if lo <= r['odds'] < hi])

    print(f'\n-- PITCHER counting props only (n={len(pit)}) '
          f'— the families the PRIME cell is made of')
    for lo, hi, lbl in BANDS:
        row(lbl, [r for r in pit if lo <= r['odds'] < hi])

    print('\n-- CONTROL 1 · inside PLUS MONEY, does conviction carry '
          'information?')
    plus = [r for r in rows if r['odds'] >= 100]
    if plus:
        for lo, hi in ((0, 60), (60, 75), (75, 85), (85, 101)):
            row(f'conviction {lo}-{hi}',
                [r for r in plus if lo <= r['conv'] < hi])
        cs = [r['conv'] for r in plus]
        ms = [r['margin'] for r in plus]
        if len(set(cs)) > 2:
            try:
                print(f'    corr(conviction, margin) in plus money = '
                      f'{statistics.correlation(cs, ms):+.3f}  n={len(plus)}')
            except Exception:                                  # noqa: BLE001
                pass

    print('\n-- CONTROL 2 · OVER vs UNDER inside plus money')
    for d in ('over', 'under'):
        row(f'{d} @ plus money', [r for r in plus if r['dir'] == d])
    print('   (an UNDER at plus money is the mirror case — the line sits')
    print('    BELOW expectation. If the cause is tail projection rather')
    print('    than an over-bias, both should fail.)')

    print('\n-- CONTROL 3 · the same bands for FAVOURITE-priced props')
    fav = [r for r in rows if r['odds'] < -120]
    row('all favourites < -120', fav)
    print('\n  ' + '=' * 68)
    print('  If plus-money margin is negative while favourite margin is not,')
    print('  the projection is wrong specifically where the line is furthest')
    print('  from the book\'s own number — a tail problem, fixable in the')
    print('  model. If both are negative, the projection is just biased and')
    print('  the price band was a red herring.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
