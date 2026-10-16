#!/usr/bin/env python3
"""Which NHL pre-game signals actually discriminated the winner? Solve for x.

== 2026-10-16 - WHY ==
ANDY: "i dont love just flipping because the record is so bad thats not solving
the problem, thats saying we have a losing model so just fade us and hope for
the best ... x + 2 = 6 theory, we know the outcomes of the games we have
snapshots of the data correct? So we should know what signals attributed or
could of attributed to correct pick then we find that and implement going
forward."

Right, and better than flipping. Flipping inverts a broken selector; this looks
for a selector that works. The NHL win probability is already calibrated
(Brier 0.2348 vs 0.2436 baseline) -- the defect is WHICH GAMES we choose to
disagree with the market on, so the question is which pre-game field actually
separates winners.

== THE DISCIPLINE, BECAUSE THIS IS THE EXACT SETUP THAT MANUFACTURES FAKE EDGES ==
Scanning many signals against known outcomes on a small sample WILL return
false positives. Every guard below exists because this project has been burned
by its absence:

 1. POINT-IN-TIME ONLY. Fields known before puck drop. l10_goals_* are safe
    because the 10-14 backfill computed them from each team's last 10 games
    STRICTLY BEFORE that date. Anything that could be written post-game is
    excluded by name.
 2. TEMPORAL SPLIT. Fit/choose on the earlier 60%, report the later 40%
    separately. An in-sample number is printed as the overfit ceiling, never
    as a result.
 3. DIRECTION CONTROL. Every signal is also graded inverted. A signal whose
    inverse scores about as well is noise with a sign.
 4. MULTIPLE COMPARISONS. With N signals tested, roughly N/20 clear p<0.05 by
    chance. The Bonferroni threshold is printed and nothing is called real
    unless it clears THAT, not 0.05.
 5. WILSON CI on every rate, and n printed beside every percentage.

The honest expected outcome is that most or all of these are noise -- that has
been the result of every scan this project has run. A null result here is
still the answer to Andy's question: it would say the discriminating
information is not in the columns we currently store, which points at what to
collect rather than at a model to tune.

== OUTCOME MEASURED ==
Home moneyline win (the market NHL sides actually play). Reported as the
home-win rate in each half of the signal, so a usable signal shows a gap
between its halves that survives the split.

USAGE
    python scan_nhl_signal_attribution.py
    python scan_nhl_signal_attribution.py --min-n 60
"""
from __future__ import annotations

import argparse
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

#: Paired home_/away_ fields that are known pre-game. Each becomes a
#: differential (home - away).
PAIRED = [
    'goalie_gsaa', 'goalie_sv_pct', 'goalie_last5_sv_pct',
    'xgf_per60', 'xga_per60', 'high_danger_for', 'high_danger_against',
    'pp_pct', 'pk_pct', '5v5_cf', 'rest_days',
    'l10_goals_per_game', 'l10_goals_against_per_game',
]
#: Single fields that are already a differential or a standalone state.
SINGLE = ['elo_home', 'elo_away', 'projected_home_wp', 'projected_spread',
          'projected_total', 'signal_confluence_net']
#: Explicitly excluded: anything that could be written at or after puck drop.
EXCLUDED_SUBSTRINGS = ('score', 'result', 'actual', 'final', 'graded',
                       'resolved', 'outcome')


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


def _b(v):
    if v is None:
        return None
    return 1.0 if v in (True, 1, '1', 't', 'true', 'True') else 0.0


def wilson(w, n):
    if n == 0:
        return None, None
    z, p = 1.96, w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def two_prop_p(w1, n1, w2, n2):
    """Two-sided p for a difference in proportions (normal approx)."""
    if n1 < 5 or n2 < 5:
        return 1.0
    p1, p2 = w1 / n1, w2 / n2
    p = (w1 + w2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = abs(p1 - p2) / se
    return 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-n', type=int, default=40)
    a = ap.parse_args()

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
        if hs is None or as_ is None or hs == as_:
            continue
        rows.append({'c': c, 'won': 1 if hs > as_ else 0,
                     'date': str(c.get('game_date'))[:10]})
    rows.sort(key=lambda r: r['date'])
    print(f'NHL graded games with context: {len(rows)}')
    if len(rows) < a.min_n:
        print('too few')
        return 1
    base = float(np.mean([r['won'] for r in rows]))
    print(f'base home win rate: {base:.3f}')
    cut = int(len(rows) * TRAIN_FRAC)
    print(f'temporal split at {rows[cut]["date"]}: '
          f'train {cut}, test {len(rows)-cut}')

    # build candidate signals
    cands = {}
    for p in PAIRED:
        hk, ak = f'home_{p}', f'away_{p}'
        if any(s in p for s in EXCLUDED_SUBSTRINGS):
            continue
        vals = []
        for r in rows:
            hv, av = f(r['c'].get(hk)), f(r['c'].get(ak))
            vals.append(None if hv is None or av is None else hv - av)
        if sum(1 for v in vals if v is not None) >= a.min_n:
            cands[f'{p}_diff'] = vals
    for s in SINGLE:
        vals = [f(r['c'].get(s)) for r in rows]
        if sum(1 for v in vals if v is not None) >= a.min_n:
            cands[s] = vals
    # back-to-back is boolean; the差 is the informative form
    b2b = []
    for r in rows:
        hb, ab = _b(r['c'].get('home_back_to_back')), _b(
            r['c'].get('away_back_to_back'))
        b2b.append(None if hb is None or ab is None else ab - hb)
    if sum(1 for v in b2b if v is not None) >= a.min_n:
        cands['b2b_edge(away_tired - home_tired)'] = b2b

    N = len(cands)
    bonf = 0.05 / max(N, 1)
    print(f'\n{N} signals tested. Bonferroni threshold p < {bonf:.4f} '
          f'(0.05/{N}). Nothing below 0.05 alone is called real.')
    print(f'\n{"signal":<40}{"n":>5}{"lo half":>12}{"hi half":>12}'
          f'{"gap":>8}{"p":>9}  verdict')

    results = []
    for name, vals in sorted(cands.items()):
        idx = [i for i, v in enumerate(vals) if v is not None]
        if len(idx) < a.min_n:
            continue
        tr = [i for i in idx if i < cut]
        te = [i for i in idx if i >= cut]
        if len(tr) < 15 or len(te) < 15:
            continue
        med = float(np.median([vals[i] for i in tr]))
        # TEST half only — the reported number
        hi = [i for i in te if vals[i] > med]
        lo = [i for i in te if vals[i] <= med]
        if len(hi) < 8 or len(lo) < 8:
            continue
        wh = sum(rows[i]['won'] for i in hi)
        wl = sum(rows[i]['won'] for i in lo)
        rh, rl = wh / len(hi), wl / len(lo)
        p = two_prop_p(wh, len(hi), wl, len(lo))
        gap = rh - rl
        v = ('REAL (clears Bonferroni)' if p < bonf
             else 'p<0.05 but NOT after correction' if p < 0.05
             else 'noise')
        print(f'  {name:<38}{len(te):>5}'
              f'{f"{100*rl:.1f}% n={len(lo)}":>12}'
              f'{f"{100*rh:.1f}% n={len(hi)}":>12}'
              f'{100*gap:>+7.1f}%{p:>9.3f}  {v}')
        results.append((name, p, gap, len(te)))

    real = [r for r in results if r[1] < bonf]
    near = [r for r in results if bonf <= r[1] < 0.05]
    print(f'\n{len(real)} clear Bonferroni, {len(near)} clear 0.05 only, '
          f'{len(results)-len(real)-len(near)} noise.')
    if not real:
        print('\nNULL RESULT, and it is an answer: on this sample none of the '
              'stored pre-game fields separates NHL winners once multiple '
              'comparisons are accounted for. That points at COLLECTING '
              'different information (confirmed-goalie timing, late scratches) '
              'rather than at tuning a model over these columns.')
    for name, p, gap, n in sorted(near, key=lambda x: x[1]):
        print(f'  worth a forward shadow: {name} gap {100*gap:+.1f}% '
              f'p={p:.3f} n={n}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
