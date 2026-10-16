#!/usr/bin/env python3
"""NHL sides: select on ABSOLUTE confidence, not on edge vs the market.

== 2026-10-16 - WHY ==
ANDY: "i dont love just flipping ... we know the outcomes of the games we have
snapshots of the data correct? So we should know what signals attributed or
could of attributed to correct pick then we find that and implement going
forward."

scan_nhl_signal_attribution.py answered it. Of 19 pre-game signals the single
strongest discriminator is OUR OWN win probability:

    projected_home_wp   low half 36.7% (n=30)  high half 68.4% (n=38)
                        gap +31.8%   p=0.009

So the model is NOT broken at ranking who wins. Meanwhile:

  * nhl sides published        10-19   34.5%
  * our edge vs market         40.3% n=62, ROI -21.3%; FLIPPED 59.7%
  * our Brier 0.2348, market's 0.2245 (the market is better)

Those three facts reconcile exactly one way: THE WP RANKS WINNERS CORRECTLY AND
DOES NOT BEAT THE PRICE. The market holds the same information slightly better,
so selecting games by DISAGREEMENT with the price selects our own noise -- which
is why the edge is anti-informative and why flipping "works". Selecting by
ABSOLUTE CONFIDENCE instead uses the part that demonstrably works.

That is the difference between fixing the selector and conceding the model.

== BUT A HIT RATE IS NOT A RESULT ==
High confidence means favourites means short prices. 68% at -180 loses money.
So every selector below is graded at the ACTUAL de-vigged moneyline the game
carried, in units, with a Wilson CI, and against two controls:

  always-HOME        the home-ice baseline this sport hands out for free
  market favourite   backing the price favourite every game

A selector only earns anything if it beats BOTH. Reported on the TEST slice of
a temporal split; the train slice is printed as the overfit ceiling and is not
the result.

USAGE
    python test_nhl_confidence_selector.py
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
TRAIN_FRAC = 0.60


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


def grade(name, bets):
    """bets: list of (hit: bool, payout_on_win: float)."""
    w = l = 0
    u = 0.0
    for hit, pay in bets:
        if hit:
            w += 1
            u += pay
        else:
            l += 1
            u -= 1.0
    n = w + l
    if n < 8:
        print(f'    {name:<34} n={n} too few')
        return None
    lo, hi = wilson(w, n)
    print(f'    {name:<34}{w}-{l}  {100*w/n:5.1f}%  CI[{lo:.1f},{hi:.1f}]  '
          f'{u:+7.2f}u  ROI {100*u/n:+6.1f}%  n={n}')
    return 100 * u / n


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
        if (hs is None or as_ is None or hs == as_ or wp is None
                or payout(hml) is None or payout(aml) is None):
            continue
        rows.append({'wp': wp, 'won': 1 if hs > as_ else 0,
                     'hml': hml, 'aml': aml,
                     'date': str(c.get('game_date'))[:10]})
    rows.sort(key=lambda r: r['date'])
    print(f'NHL games with WP + two-way moneyline + result: {len(rows)}')
    if len(rows) < 50:
        print('too few')
        return 1
    cut = int(len(rows) * TRAIN_FRAC)
    tr, te = rows[:cut], rows[cut:]
    print(f'temporal split at {rows[cut]["date"]}: train {len(tr)}, '
          f'test {len(te)}')

    for label, S in (('TRAIN (overfit ceiling, NOT the result)', tr),
                     ('TEST (the result)', te)):
        print(f'\n  == {label}  n={len(S)} ==')
        # controls
        grade('control: always HOME',
              [(r['won'] == 1, payout(r['hml'])) for r in S])
        grade('control: market favourite',
              [((r['won'] == 1) if implied(r['hml']) >= implied(r['aml'])
                else (r['won'] == 0),
                payout(r['hml'] if implied(r['hml']) >= implied(r['aml'])
                       else r['aml'])) for r in S])
        # absolute-confidence selectors, thresholds fixed from the TRAIN median
        med = float(np.median([r['wp'] for r in tr]))
        for thr, nm in ((med, f'WP > train median ({med:.3f})'),
                        (0.60, 'WP > 0.60'), (0.63, 'WP > 0.63'),
                        (0.66, 'WP > 0.66')):
            bets = [(r['won'] == 1, payout(r['hml'])) for r in S
                    if r['wp'] > thr]
            grade(f'HOME when {nm}', bets)
        for thr, nm in ((0.45, 'WP < 0.45'), (0.42, 'WP < 0.42')):
            bets = [(r['won'] == 0, payout(r['aml'])) for r in S
                    if r['wp'] < thr]
            grade(f'AWAY when {nm}', bets)

    print('\nA selector earns nothing unless it beats BOTH controls on TEST. '
          'Hit rate without the price is not a result.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
