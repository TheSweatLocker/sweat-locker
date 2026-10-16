#!/usr/bin/env python3
"""Does NHL's DISAGREEMENT with the market carry information? The real test.

== 2026-10-16 - WHY ==
NHL sides are 10-19 (34.5%) published, and across 114 graded side receipts the
failure is monotonic in price: dogs 30.4%, light favs 43.6%, mid favs 65.5%,
heavy favs 85.7%.

The obvious explanation was a flat/under-confident win probability putting fake
value on dogs. MEASURED, AND IT IS WRONG: projected_home_wp is roughly
calibrated on n=169 -- Brier 0.2348 against an always-0.58 baseline of 0.2436,
market 0.2245; bucket gaps +0.083/+0.029/-0.024/+0.040/-0.019 with no
systematic lean; logistic slope +1.21, i.e. mildly OVER-confident if anything.
A calibrated probability was not the problem.

So the problem is SELECTION, and this is the test for it.

    model = market + deviation

Accuracy comes almost entirely from the market term. Profit comes ONLY from the
deviation term. A model can be calibrated AND lose money if the games it
chooses to disagree on are the games it is wrong about -- picking on maximum
disagreement selects for model error whenever the model is no better than the
market, and the market's Brier here (0.2245) IS better than ours (0.2348).

That predicts exactly the observed price gradient: our biggest disagreements
with the market are on dogs (the market's low probability vs our higher one),
so a disagreement-driven selector concentrates picks where it is most wrong,
and those picks are dogs.

== WHAT IS MEASURED ==
For every graded NHL game with both numbers:

    edge = our P(home) - de-vigged market P(home)

bucketed, reporting actual home win rate against both. Then the same thing
graded AS A BET at the real moneyline, because a probability edge that does not
survive the price is not an edge. Plus the direction-only control: would betting
the OPPOSITE of our edge have done better?

USAGE
    python audit_nhl_edge_informative.py
"""
from __future__ import annotations

import math
import sys

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
    m = f(ml)
    if m is None:
        return None
    return (-m) / ((-m) + 100) if m < 0 else 100 / (m + 100)


def payout(ml):
    """Profit on a 1u win at this American price."""
    m = f(ml)
    if m is None:
        return None
    return (100 / (-m)) if m < 0 else (m / 100)


def wilson(w, n):
    if n == 0:
        return None, None
    z, p = 1.96, w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


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
        hml = c.get('home_ml') or c.get('close_home_ml')
        aml = c.get('away_ml') or c.get('close_away_ml')
        ph, pa = implied(hml), implied(aml)
        if (hs is None or as_ is None or wp is None or hs == as_
                or ph is None or pa is None or (ph + pa) <= 0):
            continue
        rows.append({'wp': wp, 'mkt': ph / (ph + pa),
                     'won': 1 if hs > as_ else 0,
                     'hml': hml, 'aml': aml})
    print(f'NHL games with our WP, a two-way moneyline, and a result: '
          f'{len(rows)}')
    if len(rows) < 40:
        print('too few')
        return 1

    e = np.array([r['wp'] - r['mkt'] for r in rows])
    print(f'edge = ourP(home) - marketP(home): mean {e.mean():+.4f} '
          f'sd {e.std():.4f} range [{e.min():+.3f}, {e.max():+.3f}]')

    print(f'\n{"edge bucket":<16}{"n":>5}{"ourP":>8}{"mktP":>8}'
          f'{"actual":>8}{"vs mkt":>9}')
    edges = [-1, -0.08, -0.03, 0.03, 0.08, 1]
    for lo, hi in zip(edges, edges[1:]):
        sub = [r for r in rows if lo <= (r['wp'] - r['mkt']) < hi]
        if len(sub) < 5:
            continue
        op = float(np.mean([r['wp'] for r in sub]))
        mp = float(np.mean([r['mkt'] for r in sub]))
        ac = float(np.mean([r['won'] for r in sub]))
        print(f'  {lo:+.2f}..{hi:+.2f}  {len(sub):>5}{op:>8.3f}{mp:>8.3f}'
              f'{ac:>8.3f}{ac-mp:>+9.3f}')
    print('  "vs mkt" > 0 means the market UNDER-rated the home side in that '
          'bucket, i.e. our edge was real there.')

    # Grade it as a bet: back the side our edge favours, at its real price.
    for thresh in (0.03, 0.05, 0.08):
        for flip in (False, True):
            w = l = 0
            u = 0.0
            for r in rows:
                ed = r['wp'] - r['mkt']
                if abs(ed) < thresh:
                    continue
                take_home = (ed > 0) if not flip else (ed < 0)
                ml = r['hml'] if take_home else r['aml']
                pay = payout(ml)
                if pay is None:
                    continue
                hit = (r['won'] == 1) if take_home else (r['won'] == 0)
                if hit:
                    w += 1
                    u += pay
                else:
                    l += 1
                    u -= 1.0
            n = w + l
            if n < 10:
                continue
            lo_, hi_ = wilson(w, n)
            tag = 'FLIPPED (control)' if flip else 'our edge'
            print(f'  edge>={thresh:.2f} {tag:<18} {w}-{l}  '
                  f'{100*w/n:5.1f}%  CI[{lo_:.1f},{hi_:.1f}]  '
                  f'{u:+7.2f}u  ROI {100*u/n:+6.1f}%  n={n}')
    print('\nIf FLIPPED beats "our edge" materially, the edge is ANTI-'
          'informative and the selector is the defect, not the probability.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
