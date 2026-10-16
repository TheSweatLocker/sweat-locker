#!/usr/bin/env python3
"""Is NHL's win probability calibrated? The root of the sides failure.

== 2026-10-16 - WHY ==
ANDY: "IF NHL sides is 10-19 we might as well flip them thats wildly inaccurate
for any sports model ... Lets fucking fix NHL ... by optimization i mean
improving 34.5% is unsat".

The published nhl_sides surface is 10-19 (34.5%) on n=29. Across the wider
receipt set (n=114 graded) the failure has a SHAPE, and the shape is the fix:

    dog +            7-16   30.4%   n=23
    fav -101..-149  24-31   43.6%   n=55
    fav -150..-249  19-10   65.5%   n=29
    fav -250+         6-1   85.7%   n=7

Monotonic in price. We do better the more favoured the side, which is not what
a calibrated model looks like -- a calibrated model is roughly break-even at
every price, because the price already encodes the probability. A monotonic
gradient like this says our win probability is TOO FLAT: it rates dogs higher
than they deserve and favourites lower, so "value" appears on dogs that are not
live and disappears on favourites that are.

That is a CALIBRATION defect, which is fixable, not a reason to publish less.

== WHAT THIS MEASURES ==
nhl_game_context.projected_home_wp against actual home wins, bucketed, plus the
market's own implied probability on the same games as the benchmark. Three
numbers per bucket:

    predicted   mean projected_home_wp in the bucket
    actual      share of those games the home team actually won
    market      mean implied P(home) from the moneyline, de-vigged

If predicted is pulled toward 0.5 relative to both actual and market, the model
is under-confident and the dog bias follows arithmetically.

Also reports the Brier score and the fitted logistic slope on the log-odds. A
slope below 1.0 is literal under-confidence and gives the exact recalibration
coefficient.

USAGE
    python audit_nhl_wp_calibration.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict

import numpy as np
import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H


def page(t, p):
    out, off = [], 0
    while True:
        q = dict(p, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def implied(ml):
    """American moneyline -> implied probability (with vig)."""
    m = f(ml)
    if m is None:
        return None
    return (-m) / ((-m) + 100) if m < 0 else 100 / (m + 100)


def main() -> int:
    ctx = page('nhl_game_context', {'select': '*'})
    res = page('nhl_game_results', {'select': '*'})
    by_id = {str(r.get('game_id')): r for r in res}
    by_key = {(str(r.get('game_date'))[:10], str(r.get('home_team')),
               str(r.get('away_team'))): r for r in res}

    rows = []
    for c in ctx:
        g = (by_id.get(str(c.get('game_id')))
             or by_key.get((str(c.get('game_date'))[:10],
                            str(c.get('home_team')), str(c.get('away_team')))))
        hs, as_ = f((g or {}).get('home_score')), f((g or {}).get('away_score'))
        wp = f(c.get('projected_home_wp'))
        if hs is None or as_ is None or wp is None or hs == as_:
            continue
        # de-vig the two-way moneyline where both sides are present
        ph = implied(c.get('home_ml') or c.get('close_home_ml'))
        pa = implied(c.get('away_ml') or c.get('close_away_ml'))
        mk = None
        if ph is not None and pa is not None and (ph + pa) > 0:
            mk = ph / (ph + pa)
        rows.append({'wp': wp, 'won': 1 if hs > as_ else 0, 'mkt': mk,
                     'date': str(c.get('game_date'))[:10]})
    print(f'NHL graded games with a projected_home_wp: {len(rows)}')
    if len(rows) < 40:
        print('too few to calibrate')
        return 1

    wp = np.array([r['wp'] for r in rows])
    won = np.array([r['won'] for r in rows])
    print(f'projected_home_wp: mean {wp.mean():.4f} sd {wp.std():.4f} '
          f'range [{wp.min():.3f}, {wp.max():.3f}]')
    print(f'actual home win rate: {won.mean():.4f}')
    print(f'Brier score: {float(np.mean((wp - won) ** 2)):.4f}  '
          f'(always-{won.mean():.2f} baseline '
          f'{float(np.mean((won.mean() - won) ** 2)):.4f})')

    have_mkt = [r for r in rows if r['mkt'] is not None]
    if have_mkt:
        mk = np.array([r['mkt'] for r in have_mkt])
        mw = np.array([r['won'] for r in have_mkt])
        print(f'\nmarket implied P(home), n={len(have_mkt)}: mean {mk.mean():.4f} '
              f'sd {mk.std():.4f}')
        print(f'  market Brier {float(np.mean((mk - mw) ** 2)):.4f}')
        print(f'  OUR sd {np.array([r["wp"] for r in have_mkt]).std():.4f} vs '
              f'MARKET sd {mk.std():.4f}  '
              f'-> ratio {np.array([r["wp"] for r in have_mkt]).std()/max(mk.std(),1e-9):.2f}')

    print(f'\n{"bucket":<14}{"n":>5}{"predicted":>11}{"actual":>9}{"market":>9}'
          f"{'gap':>8}")
    edges = [0.0, 0.35, 0.45, 0.50, 0.55, 0.65, 1.01]
    for lo, hi in zip(edges, edges[1:]):
        sub = [r for r in rows if lo <= r['wp'] < hi]
        if not sub:
            continue
        p = float(np.mean([r['wp'] for r in sub]))
        a = float(np.mean([r['won'] for r in sub]))
        ms = [r['mkt'] for r in sub if r['mkt'] is not None]
        mstr = f'{np.mean(ms):.3f}' if ms else '-'
        print(f'  {lo:.2f}-{hi:.2f}   {len(sub):>5}{p:>11.3f}{a:>9.3f}'
              f'{mstr:>9}{a-p:>+8.3f}')

    # logistic slope on the log-odds: <1 means under-confident
    eps = 1e-6
    x = np.log(np.clip(wp, eps, 1 - eps) / (1 - np.clip(wp, eps, 1 - eps)))
    if x.std() > 0:
        b1, b0 = np.polyfit(x, won, 1)
        print(f'\nlinear fit of outcome on our log-odds: slope {b1:+.4f}')
        # proper logistic slope via IRLS-lite on the same design
        beta = np.array([0.0, 1.0])
        X = np.column_stack([np.ones(len(x)), x])
        for _ in range(60):
            eta = X @ beta
            p_ = 1 / (1 + np.exp(-eta))
            Wd = np.clip(p_ * (1 - p_), 1e-9, None)
            z = eta + (won - p_) / Wd
            XtW = X.T * Wd
            try:
                beta_new = np.linalg.solve(XtW @ X, XtW @ z)
            except np.linalg.LinAlgError:
                break
            if np.max(np.abs(beta_new - beta)) < 1e-9:
                beta = beta_new
                break
            beta = beta_new
        print(f'LOGISTIC recalibration: intercept {beta[0]:+.4f}  '
              f'SLOPE {beta[1]:+.4f}')
        if beta[1] < 0.85:
            print(f'  -> UNDER-CONFIDENT. Our log-odds need multiplying by '
                  f'{1/beta[1]:.2f} to be calibrated. A flat probability puts '
                  f'fake value on dogs, which is the 30.4% dog record.')
        elif beta[1] > 1.15:
            print('  -> OVER-CONFIDENT.')
        else:
            print('  -> slope near 1: calibrated in shape.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
