"""Should plus-money props carry a conviction floor? And does the
projection itself break in the tail?

ANDY 2026-10-12, choosing cause over filter on the PRIME plus-money cell:
"Leave it, investigate the cause first."

WHAT THE INVESTIGATION FOUND SO FAR (MLB, since 08-01, props joined to a
real price and restricted to rows ACTUALLY BET — 918 of 2,055; the rest
were PASSed and were never a wager):

    plus money, conviction 0-60    301-387   ROI  -3.9%   n=688
    plus money, conviction 60-75    83-62    ROI +23.6%   n=145
    plus money, conviction 75-85    38-24    ROI +30.9%   n=62

Conviction carries real information in the tail; the problem is that 688 of
918 plus-money bets sit below 60, where the return is negative. The engine
half-knows this already — the bet-rate is 39% below 60 against 81% above —
but it still places the low-conviction ones.

TWO DEAD ENDS WORTH RECORDING, so they are not re-run:
  * "PRIME on dogs is broken" — the 37-bet cell that started this is a thin
    slice of pitcher counting props whose own family runs 47.4% our way on
    n=1,024, i.e. about breakeven for plus-money prices. The cell was
    variance, and gating it would have been fitting noise.
  * "OVERs at plus money are broken" (-0.21 margin, 35.8%) — that was
    driven by PASSED rows. Among rows actually BET, over is +4.2% and under
    +2.4%. Grading a population that was never wagered is how a selection
    gate gets blamed for its own correct refusals.

WHY NFL MATTERS MORE. MLB is ending; NFL is in season, and nfl_pipeline_props
stores an actual `projection` alongside `final_value`, so for NFL the
projection error can be measured directly instead of inferred from the
realized margin:

    projection bias = final_value - projection      (negative = we over-project)
    claimed edge    = projection - prop_line, signed by direction

A floor is a DELETION of published plays, so it carries the full bar: a
bootstrap CI on ROI, a temporal split, and the counterfactual on total
units rather than on the surviving percentage.

WRITES NOTHING.

CLI
    python audit_prop_conviction_floor.py --sport NFL
    python audit_prop_conviction_floor.py --sport MLB --since 2026-08-01
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML

TBL = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props'}
BANDS = [(-100000, -150, 'fav      <= -150'),
         (-150, -120, 'light fav-150..-120'),
         (-120, -100, 'near pick-120..-100'),
         (-100, 100000, 'PLUS MONEY >= +100')]
CONV = [(0, 60), (60, 75), (75, 85), (85, 101)]


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


def boot(vals, iters=8000, seed=37):
    rng = random.Random(seed)
    n = len(vals)
    o = []
    for _ in range(iters):
        t = 0.0
        for _i in range(n):
            t += vals[rng.randrange(n)]
        o.append(t / n)
    o.sort()
    return o[int(iters * 0.025)], o[int(iters * 0.975)]


def units(r):
    o = r['odds']
    if r['won'] is None:
        return None
    return (((o / 100.0) if o > 0 else (100.0 / -o)) if r['won'] else -1.0)


def line(label, rows, pad=24):
    graded = [r for r in rows if r['won'] is not None]
    n = len(graded)
    if n < 25:
        print(f'    {label:<{pad}} n={n} too thin')
        return None
    w = sum(1 for r in graded if r['won'])
    us = [units(r) for r in graded]
    roi = statistics.fmean(us) * 100
    lo, hi = boot(us)
    clears = not (lo * 100 <= 0 <= hi * 100)
    bias = [r['bias'] for r in rows if r.get('bias') is not None]
    bs = ''
    if len(bias) >= 25:
        bs = f'  projbias {statistics.fmean(bias):+5.2f}'
    print(f'    {label:<{pad}} {w}-{n - w}  hit {w / n * 100:5.1f}%  '
          f'ROI {roi:+6.1f}%  CI[{lo * 100:+6.1f},{hi * 100:+6.1f}]'
          f'{" *" if clears else "  "}  n={n}{bs}')
    return {'n': n, 'roi': roi, 'units': sum(us), 'clears': clears}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NFL', choices=sorted(TBL))
    ap.add_argument('--since', default='2026-09-01')
    a = ap.parse_args()

    props = page(TBL[a.sport], {'select': '*', 'game_date': f'gte.{a.since}'})
    rec = page('public_receipts', {
        'select': 'game_date,player_name,prop_type,pick_line,pick_odds,result,'
                  'market,sport,tier',
        'sport': f'eq.{a.sport}', 'game_date': f'gte.{a.since}'})
    R = {}
    for x in rec:
        if str(x.get('market')) != 'prop':
            continue
        o = f(x.get('pick_odds'))
        if o is None or o == 0 or abs(o) > 2000:
            continue
        k = (str(x.get('game_date'))[:10],
             str(x.get('player_name')).strip().lower(),
             str(x.get('prop_type')), str(f(x.get('pick_line'))))
        R[k] = (o, str(x.get('result') or '').strip().lower())

    rows = []
    for p in props:
        pl = f(p.get('prop_line'))
        if pl is None:
            continue
        d = str(p.get('direction') or '').lower()
        if d not in ('over', 'under'):
            continue
        k = (str(p.get('game_date'))[:10],
             str(p.get('player_name')).strip().lower(),
             str(p.get('prop_type')), str(pl))
        if k not in R:
            continue
        o, res = R[k]
        if res in ('no_action', 'no action', 'void', 'no play'):
            continue            # PASS was never a bet
        won = (True if res in ('win', 'won')
               else False if res in ('loss', 'lost') else None)
        fv, pj = f(p.get('final_value')), f(p.get('projection'))
        rows.append({'odds': o, 'won': won,
                     'conv': f(p.get('conviction')) or 0.0,
                     'date': str(p.get('game_date'))[:10], 'dir': d,
                     'bias': (fv - pj) if (fv is not None and pj is not None)
                     else None,
                     'claimed': ((pj - pl) if d == 'over' else (pl - pj))
                     if pj is not None else None})

    print(f'=== prop conviction floor · {a.sport} · since {a.since} ===')
    print(f'    {len(props)} prop rows · {len(rows)} BET and joined to a price')
    print('    PASS/NO_ACTION excluded — it was never a wager\n')
    if len(rows) < 60:
        print('  not enough bet rows to judge')
        return 0

    nb = sum(1 for r in rows if r.get('bias') is not None)
    print(f'    {nb} carry a stored projection '
          f'(projbias = final_value - projection; negative = we over-project)')

    for lo, hi, lbl in BANDS:
        band = [r for r in rows if lo <= r['odds'] < hi]
        if len([r for r in band if r['won'] is not None]) < 25:
            continue
        print(f'\n-- {lbl}  (n={len(band)})')
        for clo, chi in CONV:
            line(f'conviction {clo}-{chi}',
                 [r for r in band if clo <= r['conv'] < chi])
        # the floor, as one cut
        below = [r for r in band if r['conv'] < 60]
        above = [r for r in band if r['conv'] >= 60]
        print('    ' + '-' * 60)
        r_below = line('BELOW 60 (floor cuts)', below)
        r_above = line('60 AND ABOVE (survives)', above)
        if r_below and r_above:
            tot = r_below['units'] + r_above['units']
            n_tot = r_below['n'] + r_above['n']
            print(f'    counterfactual: band is {tot:+.2f}u on n={n_tot} '
                  f'(ROI {tot / n_tot * 100:+.1f}%); with a 60 floor it is '
                  f'{r_above["units"]:+.2f}u on n={r_above["n"]} '
                  f'(ROI {r_above["roi"]:+.1f}%)')
            ds = sorted(r['date'] for r in below if r['won'] is not None)
            if len(ds) >= 60:
                mid = ds[len(ds) // 2]
                print(f'    temporal split of the CUT population at {mid}:')
                line('  below-60 early', [r for r in below if r['date'] < mid])
                line('  below-60 late', [r for r in below if r['date'] >= mid])

    print('\n  ' + '=' * 68)
    print('  A floor is only justified where the CUT population loses with a')
    print('  CI excluding zero, in BOTH halves, and removing it improves')
    print('  total units. Anything less and we would be deleting live plays')
    print('  to chase a number.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
