#!/usr/bin/env python3
"""What would the retrained v4 have done to NFL THIS SEASON? -- LEAKY, SEE BELOW.

██ 2026-10-16 · THIS SCRIPT'S NUMBERS ARE LOOKAHEAD-INFLATED. DO NOT QUOTE ██
It scores past 2026 games with features from nfl_v4_inference.build_current_epa(),
which ends with

    off_latest = off.loc[off.groupby('posteam')['week'].idxmax()]

i.e. ONE EPA value per team, from that team's LATEST played week. That is
exactly right for predicting an UPCOMING game -- the newest data is the best
data -- and it is a leak for every PAST game, which gets scored with EPA drawn
partly from weeks after it was played. A week-2 game is predicted using week-6
form.

First run produced NEW v4 at 62.9-65.7% and ROI +20.0% to +25.5% across all
four signals, against the 2024 walk-forward holdout which says the same four
signals LOSE (-2.8% to -8.5%). Opposite directions on the same signals is the
tell. Trust the holdout.

The trustworthy assessment is backtest_v4_signal_impact.py: trained 2020-2023,
scored on a 2024 holdout the model never saw, features built walk-forward with
.shift(1). Its verdict stands -- the retrained v4 improves all four signals and
all four still lose.

Kept because the SHAPE metrics it reports are leak-free and worth having:
prediction sd, range, and how many games flip an edge signal. Those depend on
the model, not on the outcome. Everything with a W-L attached is not.

ORIGINAL HEADER:
What would the retrained v4 have done to NFL this season?

== 2026-10-16 - WHY ==
ANDY: "Can we assess what v4 changes would have done to nfl?"

backtest_v4_signal_impact.py answered this on the 2024 holdout. This answers it
on 2026 — the games that actually happened, with the lines we actually posted
and the picks we actually published. Different question, and the one that
matters.

Both models are loaded and scored on the SAME rows, so the only variable is the
model. The four ENABLED rules that read ctx.v4_spread:

    nfl_home_spread_edge          v4 - close_spread >= 2.0   -> HOME_RL
    nfl_away_spread_edge          close_spread - v4 >= 2.0   -> AWAY_RL
    nfl_spread_consensus_home_rl  counts a v4 vote at +/-0.5
    nfl_spread_consensus_away_rl  ditto

SIGN: nfl_game_context.close_spread is the market's implied HOME margin
(project_close_spread_sign_bug_914, n=7,325). The assertions below refuse to
run unless the line correlates positively with the actual margin and
always-HOME sits near 50% — a first pass of the 2024 version produced 81-94%
ATS on BOTH sides at once purely from a sign error, which is impossible and was
caught only because those checks were added.

ALSO REPORTED: what the published pick did on the same games, so the v4 signals
are measured against what actually shipped rather than in the abstract.

USAGE
    python assess_v4_on_2026.py
"""
from __future__ import annotations

import json
import math
import pickle
import sys
import warnings
from collections import Counter

warnings.filterwarnings('ignore')

import numpy as np
import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
PRICE = -110
BREAKEVEN = 100 * abs(PRICE) / (abs(PRICE) + 100)
EDGE, CONS_T = 2.0, 0.5


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


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                      # noqa: BLE001
            return {}
    return v if isinstance(v, dict) else {}


def wilson(w, n):
    if n == 0:
        return None, None
    z, p = 1.96, w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def grade(name, picks):
    w = l = p = 0
    u = 0.0
    for took_home, cs, margin in picks:
        edge = margin - cs
        if abs(edge) < 1e-9:
            p += 1
            continue
        if (edge > 0) == took_home:
            w += 1
            u += 100 / abs(PRICE)
        else:
            l += 1
            u -= 1.0
    n = w + l
    if n == 0:
        print(f'    {name:<32} fires 0 times')
        return
    lo, hi = wilson(w, n)
    verdict = ('BEATS breakeven' if lo > BREAKEVEN
               else 'LOSES, CI clears' if hi < BREAKEVEN else 'inconclusive')
    print(f'    {name:<32} {w}-{l}  {100*w/n:5.1f}%  CI[{lo:.1f},{hi:.1f}]  '
          f'{u:+7.2f}u  ROI {100*u/n:+6.1f}%  {verdict}'
          f'{f"  ({p} push)" if p else ""}')


def main() -> int:
    import nfl_v4_inference as INF

    ctx = page('nfl_game_context', {'select': '*'})
    res = page('nfl_game_results', {'select': '*'})
    by_id = {str(r.get('game_id')): r for r in res}
    by_key = {(str(r.get('game_date'))[:10], str(r.get('home_team')),
               str(r.get('away_team'))): r for r in res}

    with open('models/nfl_v4_spread.pkl', 'rb') as fh:
        NEW = pickle.load(fh)
    with open('models/nfl_v4_spread.pkl.bak_20261014', 'rb') as fh:
        OLD = pickle.load(fh)
    print(f'NEW {len(NEW["feature_cols"])} features | '
          f'OLD {len(OLD["feature_cols"])} features')
    team_epa = INF.build_current_epa()
    print(f'EPA for {len(team_epa)} teams\n')

    rows = []
    for c in ctx:
        if c.get('stats_source') == 'preseason':
            continue
        g = (by_id.get(str(c.get('game_id')))
             or by_key.get((str(c.get('game_date'))[:10],
                            str(c.get('home_team')), str(c.get('away_team')))))
        hs, as_ = f((g or {}).get('home_score')), f((g or {}).get('away_score'))
        cs = f(c.get('close_spread'))
        if hs is None or as_ is None or cs is None:
            continue
        fn_new = INF.game_to_features(c, team_epa, NEW['feature_cols'])
        fn_old = INF.game_to_features(c, team_epa, OLD['feature_cols'])
        if fn_new is None or fn_old is None:
            continue
        rows.append({'c': c, 'cs': cs, 'margin': hs - as_,
                     'new': float(NEW['model'].predict(np.array([fn_new]))[0]),
                     'old': float(OLD['model'].predict(np.array([fn_old]))[0])})
    print(f'2026 graded NFL games with a line and both models: {len(rows)}')
    if len(rows) < 30:
        print('too few to assess')
        return 1

    cs = np.array([r['cs'] for r in rows])
    mg = np.array([r['margin'] for r in rows])
    corr = float(np.corrcoef(cs, mg)[0, 1])
    cover = float(((mg - cs) > 0).mean())
    print(f'SIGN CHECK  corr(line, margin) {corr:+.3f}  '
          f'always-HOME ATS {100*cover:.1f}%')
    assert corr > 0.3, f'line correlates {corr:+.3f} — sign is wrong'
    assert 0.35 < cover < 0.65, f'always-HOME {100*cover:.1f}% — not a fair line'

    for label, key in (('OLD v4 (constant)', 'old'),
                       ('NEW v4 (team features)', 'new')):
        p = np.array([r[key] for r in rows])
        print(f'\n  {label}  sd {p.std():.3f}  '
              f'range [{p.min():+.2f}, {p.max():+.2f}]  '
              f'negative {int((p < 0).sum())}/{len(p)}')
        home = [(True, r['cs'], r['margin']) for r in rows
                if r[key] - r['cs'] >= EDGE]
        away = [(False, r['cs'], r['margin']) for r in rows
                if r['cs'] - r[key] >= EDGE]
        grade('nfl_home_spread_edge', home)
        grade('nfl_away_spread_edge', away)
        vh = [(True, r['cs'], r['margin']) for r in rows
              if r[key] > r['cs'] + CONS_T]
        va = [(False, r['cs'], r['margin']) for r in rows
              if r[key] < r['cs'] - CONS_T]
        grade('consensus: v4 vote HOME', vh)
        grade('consensus: v4 vote AWAY', va)

    # what the PUBLISHED pick did on the same games
    print('\n  PUBLISHED primary_play on the same games, for comparison:')
    pub, tiers = [], Counter()
    for r in rows:
        pp = jd(r['c'].get('primary_play'))
        side = str(pp.get('side') or '').upper()
        if side not in ('HOME', 'AWAY'):
            continue
        tiers[f"{pp.get('type')}:{pp.get('tier')}"] += 1
        pub.append((side == 'HOME', r['cs'], r['margin']))
    grade('published (all tiers)', pub)
    print(f'    tier mix: {dict(tiers)}')

    # how often do the two models disagree on the SIDE they imply?
    flips = sum(1 for r in rows
                if ((r['old'] - r['cs'] >= EDGE) != (r['new'] - r['cs'] >= EDGE))
                or ((r['cs'] - r['old'] >= EDGE) != (r['cs'] - r['new'] >= EDGE)))
    print(f'\n  games where an edge signal changes state: {flips}/{len(rows)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
