#!/usr/bin/env python3
"""Does a model beat a CONSTANT? The control that kills fake level signals.

== 2026-10-14 - WHY THIS EXISTS ==
The rolling-form voter's one surviving result was NHL total: slope of the
market's residual on our deviation = +0.567, CI[+0.202, +0.900], n=1,067, out
of sample. That was the only CI-clearing result of the week.

Then the production writer printed its prediction spread: mean +6.11, sd 0.12.
A model that says 6.11 every single night is a CONSTANT. And for a constant c,

    deviation = c - market

so the deviation is just the market's own level, negated and shifted. Regress
the market's residual on that and you are asking "does the market's level
predict the market's error" -- a real question about the market, and nothing
whatever to do with our model. NFL's totals projection already proved this is
not hypothetical: pinned near 44.5, it produced slope -0.908 with a CI
excluding zero, and the mechanism was exactly this arithmetic.

So any level model reporting an informative slope has to clear one more bar:
BEAT A CONSTANT. The constant predictor here is the mean outcome over the same
TRAIN slice, carrying no game-specific information by construction. If the
constant scores the same slope, the model's slope is the market's level in
disguise.

WHAT IS REPORTED, per sport and market:
  * prediction sd            -- does the model differentiate games at all?
  * MAE vs the market's MAE
  * slope of residual on deviation, for THE MODEL and for THE CONSTANT
  * the gap between them, which is the only part attributable to the model

VERDICTS
  CONSTANT ARTEFACT  the constant's slope CI overlaps the model's -- the model
                     has added nothing measurable over saying one number
  REAL               the model's slope CI clears zero AND sits clear of the
                     constant's
  noise              model slope CI includes zero

USAGE
    python audit_constant_control.py
    python audit_constant_control.py --sport NHL --market total
"""
from __future__ import annotations

import argparse
import math
import random
import sys

import numpy as np

import build_panelist_records as B
import voter_rolling_form as V

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

TRAIN_FRAC = 0.6
MIN_ROWS = 120


def boot_slope(x, y, n=1200, seed=17):
    rnd = random.Random(seed)
    xa, ya = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    k = len(xa)
    if k < 10:
        return None, None
    out = []
    for _ in range(n):
        idx = np.array([rnd.randrange(k) for _ in range(k)])
        xx, yy = xa[idx], ya[idx]
        if xx.std() == 0:
            continue
        out.append(float(np.polyfit(xx, yy, 1)[0]))
    if not out:
        return None, None
    out.sort()
    return out[int(0.025 * len(out))], out[int(0.975 * len(out))]


def market_of(sport, market, g):
    """The market number this prediction is measured against."""
    if market == 'total':
        return B._f(g.get('close_total'))
    # MLB/NHL have no usable margin baseline: their close_spread /
    # close_puckline is a fixed run/puck line, not a margin forecast.
    if sport in ('MLB', 'NHL'):
        return None
    cs = B._f(g.get('close_spread'))
    if cs is None:
        return None
    home_line = -cs if sport == 'NFL' else cs
    return -home_line


def run(sport: str, market: str) -> None:
    vm = 'margin' if market == 'margin' else 'total'
    rows = V.load(sport, vm)
    # Join feature_history straight to RESULTS. Going via *_game_context loses
    # most of the history -- nhl_game_context holds 202 rows against 2,965
    # graded games -- and the closing line lives on the results table anyway.
    res = {str(r.get('game_id')): r
           for r in V.page(V.RESULTS[sport], {'select': '*'})}
    data = []
    for r in rows:
        g = res.get(r['game_id'])
        if not g or r['y'] is None:
            continue
        mkt = market_of(sport, vm, g)
        if mkt is None:
            continue
        data.append({'date': r['date'], 'f': r['f'], 'y': r['y'], 'mkt': mkt})
    data.sort(key=lambda x: x['date'])
    print(f'\n{"="*78}\n{sport} - {market.upper()}   {len(data)} rows with '
          f'inputs + result + market')
    if len(data) < MIN_ROWS:
        print(f'  below MIN_ROWS={MIN_ROWS}')
        return
    cut = int(len(data) * TRAIN_FRAC)
    tr, te = data[:cut], data[cut:]
    specs = V.FEATS[vm]
    beta = V.fit([{'f': d['f'], 'y': d['y']} for d in tr], specs)
    if beta is None:
        print('  cannot fit')
        return
    print(f'  temporal split at {data[cut]["date"]}: train {len(tr)} '
          f'test {len(te)}')

    y_te = np.array([d['y'] for d in te], dtype=float)
    mkt_te = np.array([d['mkt'] for d in te], dtype=float)
    pred = np.array([V.predict(beta, {'f': d['f']}, specs) for d in te],
                    dtype=float)
    # The control: the TRAIN mean. No game-specific information, by build.
    const = float(np.mean([d['y'] for d in tr]))
    const_te = np.full(len(te), const)

    resid = y_te - mkt_te
    print(f'  market MAE {np.mean(np.abs(resid)):.3f}   '
          f'model MAE {np.mean(np.abs(y_te - pred)):.3f}   '
          f'constant({const:.2f}) MAE {np.mean(np.abs(y_te - const_te)):.3f}')
    print(f'  model prediction sd {pred.std():.3f}  '
          f'(market sd {mkt_te.std():.3f})')
    if pred.std() < 0.05 * max(mkt_te.std(), 1e-9):
        print('  ** model prediction is essentially CONSTANT relative to the '
              'market **')

    out = {}
    for label, p in (('model', pred), ('constant', const_te)):
        dev = p - mkt_te
        if dev.std() < 1e-9:
            print(f'  {label:<9} never deviates')
            out[label] = (None, None, None)
            continue
        sl = float(np.polyfit(dev, resid, 1)[0])
        lo, hi = boot_slope(dev, resid)
        out[label] = (sl, lo, hi)
        cis = f'[{lo:+.3f}, {hi:+.3f}]' if lo is not None else 'n/a'
        print(f'  {label:<9} dev sd {dev.std():>6.3f}  slope {sl:+7.3f}  '
              f'CI {cis}')

    msl, mlo, mhi = out.get('model', (None, None, None))
    csl, clo, chi = out.get('constant', (None, None, None))
    if msl is None or mlo is None:
        verdict = 'no verdict'
    elif not (mlo > 0 or mhi < 0):
        verdict = 'noise — model slope CI includes zero'
    elif csl is not None and clo is not None and not (mlo > chi or mhi < clo):
        verdict = ('CONSTANT ARTEFACT — the constant scores the same slope, '
                   'so this is the market\'s own level, not our model')
    else:
        verdict = 'REAL — clears zero AND clears the constant'
    print(f'  VERDICT: {verdict}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(B.RESULTS))
    ap.add_argument('--market', choices=('margin', 'total'))
    a = ap.parse_args()
    sports = ([a.sport] if a.sport
              else ['NFL', 'NCAAF', 'NHL', 'NBA', 'MLB', 'NCAAB'])
    markets = [a.market] if a.market else ['margin', 'total']
    print('CONSTANT CONTROL — a level model must beat saying one number.')
    for s in sports:
        for m in markets:
            try:
                run(s, m)
            except Exception as e:                             # noqa: BLE001
                print(f'\n{s} - {m}: FAILED {type(e).__name__}: {str(e)[:120]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
