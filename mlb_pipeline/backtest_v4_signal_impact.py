#!/usr/bin/env python3
"""What do the four live v4 signals DO, under old v4 vs new v4?

== 2026-10-14 - WHY THIS EXISTS ==
Retraining v4 (nfl_v4_train.py, commit 80216e78) replaced a constant with a
real model: prediction sd 0.302 -> 3.845, correlation with the actual margin
+0.025 -> +0.342. nfl_v4_inference.py runs in nfl_pipeline.yml:785, so the new
values reach nfl_game_context on the next nightly run.

FOUR ENABLED signal_sources rules read ctx.v4_spread:

    nfl_home_spread_edge          v4_spread - close_spread >= 2.0
    nfl_away_spread_edge          close_spread - v4_spread >= 2.0
    nfl_spread_consensus_home_rl  counts v3 AND v4 as agreeing votes
    nfl_spread_consensus_away_rl  counts v3 AND v4 as agreeing votes

Under the OLD v4 none of them was a model signal. With v4 pinned at about +1.5
and NFL close_spread being the market's implied HOME margin:

    nfl_away_spread_edge  fired whenever close_spread >= 3.5  -- "fade the home
                          favourite", on every such game, with no model behind it
    nfl_home_spread_edge  fired whenever close_spread <= -0.5 -- "back the home
                          dog", likewise

They were filters on the line wearing a model's name. The consensus pair was
counting a constant as one of its votes, which is the exact lens-count
inflation the 2026-09-27 note in nfl_game_context.py:1094-1112 described:
"it inflates the lens count without adding information, which is how a 2/5 gate
silently becomes a 2/4 gate".

So this is not a question of whether the new model is better in the abstract --
it demonstrably is. It is whether four signals that have been firing on a
constant should start firing on a model WITHOUT anyone having graded the
difference. This grades it, on the 2024 holdout, before the nightly runs.

Graded ATS at -110 against the closing spread, which is the market these
signals emit (HOME_RL / AWAY_RL).

USAGE
    python backtest_v4_signal_impact.py
"""
from __future__ import annotations

import math
import pickle
import sys
import warnings

warnings.filterwarnings('ignore')

import numpy as np

sys.path.insert(0, '.')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

PRICE = -110
BREAKEVEN = 100 * abs(PRICE) / (abs(PRICE) + 100)
EDGE = 2.0
CONS_T = 0.5


def wilson(w, n):
    if n == 0:
        return None, None
    z, p = 1.96, w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def grade(name, picks):
    """picks: list of (took_home, close_spread, actual_margin)."""
    w = l = p = 0
    u = 0.0
    for took_home, cs, margin in picks:
        # NFL close_spread is the market's implied HOME margin, so the home
        # side covers when the actual margin exceeds it.
        edge = margin - cs
        if abs(edge) < 1e-9:
            p += 1
            continue
        hit = (edge > 0) == took_home
        if hit:
            w += 1
            u += 100 / abs(PRICE)
        else:
            l += 1
            u -= 1.0
    n = w + l
    if n == 0:
        print(f'    {name:<34} fires 0 times')
        return
    hit = 100 * w / n
    lo, hi = wilson(w, n)
    verdict = ('BEATS breakeven' if lo > BREAKEVEN
               else 'LOSES, CI clears' if hi < BREAKEVEN
               else 'inconclusive')
    print(f'    {name:<34} {w}-{l}  {hit:5.1f}%  CI[{lo:.1f},{hi:.1f}]  '
          f'{u:+7.2f}u  ROI {100*u/n:+6.1f}%  {verdict}'
          f'{f"  ({p} push)" if p else ""}')


def main() -> int:
    import nfl_v4_train as T

    df = T.load_training_data([2020, 2021, 2022, 2023, 2024])
    val = df[df['season'] == 2024].copy()

    # ══ SIGN, VERIFIED NOT ASSUMED ══
    # First pass negated spread_line and produced 81-94% ATS on BOTH sides at
    # once, which is impossible: on any game one side covers and the other does
    # not. nfl_data_py's spread_line is POSITIVE when the HOME team is
    # favoured, so it already IS the market's implied home margin and must not
    # be negated. Negating it made `margin - cs` the sum of two same-signed
    # quantities on every home favourite, which reads as a cover almost always.
    # The assertion below refuses to run if the sign ever flips again.
    col = 'spread_line' if 'spread_line' in val.columns else None
    if col is None:
        print('no spread_line in schedules — cannot grade ATS')
        return 1
    val = val[val[col].notna()].copy()
    val['mkt_home_margin'] = val[col].astype(float)

    # A correctly-signed line must correlate POSITIVELY with the actual home
    # margin, and always-home must sit near 50% ATS, not 80%.
    _c = float(np.corrcoef(val['mkt_home_margin'], val['spread_actual'])[0, 1])
    _cover = float(((val['spread_actual'] - val['mkt_home_margin']) > 0).mean())
    print(f'  SIGN CHECK  corr(line, actual margin) {_c:+.3f}  '
          f'always-HOME ATS {100*_cover:.1f}%')
    assert _c > 0.3, f'line correlates {_c:+.3f} with margin — sign is wrong'
    assert 0.40 < _cover < 0.60, (
        f'always-HOME covers {100*_cover:.1f}% — not a fair line, grading is '
        f'broken')

    print('V4 SIGNAL IMPACT — the four live rules, old v4 vs new v4')
    print(f'2024 holdout, {len(val)} games with a closing spread. '
          f'ATS at -110, breakeven {BREAKEVEN:.2f}%.\n')

    preds = {}
    for label, path in (('OLD (constant)', 'models/nfl_v4_spread.pkl.bak_20261014'),
                        ('NEW (team feats)', 'models/nfl_v4_spread.pkl')):
        with open(path, 'rb') as f:
            pkg = pickle.load(f)
        fc = pkg['feature_cols']
        if any(c not in val.columns for c in fc):
            print(f'{label}: missing columns, skipping')
            continue
        sub = val.dropna(subset=list(fc) + ['spread_actual', 'mkt_home_margin'])
        preds[label] = (sub, pkg['model'].predict(sub[fc].values))

    for label, (sub, p) in preds.items():
        cs = sub['mkt_home_margin'].values.astype(float)
        margin = sub['spread_actual'].values.astype(float)
        print(f'  {label}   n={len(sub)}  v4 sd {p.std():.3f}  '
              f'range [{p.min():+.2f}, {p.max():+.2f}]')

        home = [(True, cs[i], margin[i]) for i in range(len(p))
                if p[i] - cs[i] >= EDGE]
        away = [(False, cs[i], margin[i]) for i in range(len(p))
                if cs[i] - p[i] >= EDGE]
        grade('nfl_home_spread_edge', home)
        grade('nfl_away_spread_edge', away)

        # The consensus pair needs v3. v3 = projected + a rest nudge and is not
        # reproducible here, so the v4 HALF of the vote is graded on its own:
        # the vote v4 contributes to each side.
        vh = [(True, cs[i], margin[i]) for i in range(len(p))
              if p[i] > cs[i] + CONS_T]
        va = [(False, cs[i], margin[i]) for i in range(len(p))
              if p[i] < cs[i] - CONS_T]
        grade('consensus: v4 vote HOME', vh)
        grade('consensus: v4 vote AWAY', va)
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
