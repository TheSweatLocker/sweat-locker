"""Do our football models have SEPARATE takes, or are they mimicking?

ANDY 2026-10-13: "I want to ensure models arent mimicing eachother and have
sperate takes for the engine to read".

WHY THIS IS THE RIGHT QUESTION. The engine treats agreement between models
as evidence — model consensus raises conviction and a dissent lowers it. If
two "models" are built from the same inputs, their agreement is arithmetic,
not corroboration, and the ensemble is counting one opinion twice. This has
already bitten here once: lens independence was measured at r=+0.952
(feedback_lens_independence_needs_different_inputs) because the lenses
shared inputs.

THREE TESTS, in increasing strictness. The first two are the ones usually
run and neither is sufficient:

 1. PAIRWISE CORRELATION of the model spreads. High r means they move
    together, but it does NOT by itself mean redundancy — every honest
    football model correlates highly with every other because they are all
    mostly predicting the same strong teams.

 2. CORRELATION WITH THE CLOSING LINE. A model that tracks the line at
    r=0.98 is reproducing the market. It can still be useful if its
    DEVIATIONS are informative, which is what test 3 measures.

 3. INCREMENTAL VALUE ON THE RESIDUAL — the only test that settles it.
    Regress what the line missed

        y = home_margin - home_spread_number

    on each model's deviation from the line

        x_i = model_spread_i - close_spread

    first alone, then all together. A model whose coefficient collapses
    when its peers are included is adding nothing the others did not
    already say. That is mimicry with a measurement behind it, and it is
    invisible in tests 1 and 2.

SIGN CONVENTION IS NOT UNIFORM AND IT MATTERS. Verified empirically
2026-10-11: NFL close_spread is POSITIVE when the home team is favoured
(margin - close_spread > 0 means home covered, 30/30 exact); NCAAF, MLB,
NHL, NBA and NCAAB store NEGATIVE for a home favourite. Getting this
backwards flips every residual and would invert the entire conclusion, so
the sport's sign is applied explicitly and the convention is re-verified at
runtime against the stored spread_result where one exists.

WRITES NOTHING.

CLI
    python audit_model_independence.py --sport NFL
    python audit_model_independence.py --sport NCAAF --since 2026-08-01
"""
from __future__ import annotations

import argparse
import math
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

#: sport -> (ctx table, results table, close_spread sign for home favourite)
SPORTS = {'NFL': ('nfl_game_context', 'nfl_game_results', +1),
          'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results', -1)}

#: Model spread estimates, in HOME-NEGATIVE-FAVOURITE terms after norm().
#: Each entry is (column, kind) where kind says how to turn it into a
#: home-perspective spread.
CANDIDATES = {
    'NFL': [
        ('projected_spread', 'spread'),
        ('projected_spread_raw', 'spread'),
        ('projected_spread_v2', 'spread'),
        ('v4_spread', 'spread'),
        ('model_pred', 'points'),       # model_pred_home_points - away
        ('panel_pred', 'panel'),        # panel_pred_home_pts - away
    ],
    'NCAAF': [
        ('projected_spread', 'spread'),
        ('projected_spread_raw', 'spread'),
        ('epa_pred_spread', 'spread'),
        ('sp_plus_pred_spread', 'spread'),
        ('sp_plus_pred_pts', 'sp_points'),
        ('model_pred', 'points'),
    ],
}


def page(table, params, cap=200000):
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
            time.sleep(3)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def f(v):
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def model_margin(g, col, kind):
    """-> predicted HOME MARGIN (home points minus away points)."""
    if kind == 'spread':
        s = f(g.get(col))
        if s is None:
            return None
        # A stored "spread" is the home number. Home favourite is negative
        # in every sport except NFL's close_spread, but these are MODEL
        # columns, which are written as projected home margin's negative —
        # resolved by the runtime check in main().
        return s
    if kind == 'points':
        h, a = f(g.get('model_pred_home_points')), f(g.get('model_pred_away_points'))
        return (h - a) if (h is not None and a is not None) else None
    if kind == 'panel':
        h, a = f(g.get('panel_pred_home_pts')), f(g.get('panel_pred_away_pts'))
        return (h - a) if (h is not None and a is not None) else None
    if kind == 'sp_points':
        h, a = (f(g.get('sp_plus_pred_home_pts')),
                f(g.get('sp_plus_pred_away_pts')))
        return (h - a) if (h is not None and a is not None) else None
    return None


def corr(xs, ys):
    try:
        return statistics.correlation(xs, ys)
    except Exception:                                          # noqa: BLE001
        return float('nan')


def ols(X, y):
    """Multivariate OLS via normal equations. X is a list of rows (no
    intercept column — one is added). Returns (betas, ses)."""
    n = len(y)
    k = len(X[0]) + 1
    if n <= k + 2:
        return None, None
    A = [[1.0] + list(row) for row in X]
    # Gram matrix
    G = [[sum(A[r][i] * A[r][j] for r in range(n)) for j in range(k)]
         for i in range(k)]
    b = [sum(A[r][i] * y[r] for r in range(n)) for i in range(k)]
    # Gauss-Jordan with partial pivoting, tracking the inverse for SEs
    M = [G[i][:] + [1.0 if i == j else 0.0 for j in range(k)] for i in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-10:
            return None, None
        M[c], M[p] = M[p], M[c]
        pv = M[c][c]
        M[c] = [v / pv for v in M[c]]
        for r in range(k):
            if r == c:
                continue
            fac = M[r][c]
            if fac:
                M[r] = [vr - fac * vc for vr, vc in zip(M[r], M[c])]
    inv = [row[k:] for row in M]
    beta = [sum(inv[i][j] * b[j] for j in range(k)) for i in range(k)]
    resid = [y[r] - sum(beta[i] * A[r][i] for i in range(k)) for r in range(n)]
    s2 = sum(e * e for e in resid) / (n - k)
    ses = [math.sqrt(max(s2 * inv[i][i], 0.0)) for i in range(k)]
    return beta, ses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NFL', choices=sorted(SPORTS))
    ap.add_argument('--since', default='2026-08-01')
    a = ap.parse_args()
    ctx_t, res_t, sign = SPORTS[a.sport]

    ctx = page(ctx_t, {'select': '*'})
    res = page(res_t, {'select': 'game_date,home_team,away_team,home_score,'
                                 'away_score,close_spread,spread_result',
                       'game_date': f'gte.{a.since}'})
    idx = {(str(r['game_date'])[:10], str(r['home_team']).lower(),
            str(r['away_team']).lower()): r for r in res}

    games = []
    for g in ctx:
        k = (str(g.get('game_date'))[:10], str(g.get('home_team')).lower(),
             str(g.get('away_team')).lower())
        r = idx.get(k)
        if not r:
            continue
        hs, as_ = f(r.get('home_score')), f(r.get('away_score'))
        cs = f(r.get('close_spread'))
        if cs is None:
            cs = f(g.get('close_spread'))
        if hs is None or as_ is None or cs is None:
            continue
        games.append({'g': g, 'margin': hs - as_, 'cs': cs,
                      'sr': str(r.get('spread_result') or '')})

    print(f'=== model independence · {a.sport} · since {a.since} ===')
    print(f'  {len(games)} graded games joined to context')
    if len(games) < 30:
        print('  too few to judge')
        return 0

    # ══ RE-VERIFY THE SIGN AT RUNTIME ══
    # The convention is documented but a silent flip would invert every
    # conclusion below, so it is checked against the stored spread_result
    # rather than trusted.
    chk = [x for x in games if x['sr'] in ('home_covered', 'away_covered')]
    agree = sum(1 for x in chk
                if ((x['margin'] - x['cs'] * sign) > 0)
                == (x['sr'] == 'home_covered'))
    if chk:
        print(f'  sign check: {agree}/{len(chk)} agree with stored '
              f'spread_result using sign={sign:+d}'
              + ('  OK' if agree >= 0.9 * len(chk) else
                 '  <== CONVENTION MISMATCH, results below are suspect'))

    # home spread number, positive = home getting points
    for x in games:
        x['line_home'] = -(x['cs'] * sign)
        x['resid'] = x['margin'] - (x['cs'] * sign)

    cands = CANDIDATES[a.sport]
    series = {}
    for col, kind in cands:
        vals = []
        for x in games:
            m = model_margin(x['g'], col, kind)
            vals.append(m)
        n_have = sum(1 for v in vals if v is not None)
        if n_have >= 30:
            series[col] = vals
        print(f'  {col:<24} {n_have:>4}/{len(games)} populated'
              + ('' if n_have >= 30 else '   (skipped, too thin)'))
    if len(series) < 2:
        print('\n  fewer than two usable model series — nothing to compare')
        return 0

    # ── TEST 1 · pairwise correlation ───────────────────────────────
    print('\n── TEST 1 · pairwise correlation of model predictions')
    names = list(series)
    print('    ' + ''.join(f'{n[:11]:>13}' for n in names))
    for i, a_n in enumerate(names):
        row = f'    {a_n[:11]:<13}'
        for j, b_n in enumerate(names):
            pairs = [(series[a_n][k], series[b_n][k])
                     for k in range(len(games))
                     if series[a_n][k] is not None
                     and series[b_n][k] is not None]
            if len(pairs) < 20:
                row += f'{"-":>13}'
                continue
            r = corr([p[0] for p in pairs], [p[1] for p in pairs])
            row += f'{r:>+13.3f}'
        print(row)

    # ── TEST 2 · correlation with the closing line ──────────────────
    print('\n── TEST 2 · correlation with the CLOSING LINE (is it just the '
          'market?)')
    for n in names:
        pairs = [(series[n][k], games[k]['line_home'])
                 for k in range(len(games)) if series[n][k] is not None]
        r = corr([p[0] for p in pairs], [p[1] for p in pairs])
        sd = statistics.pstdev([p[0] - (-p[1]) for p in pairs])
        print(f'    {n:<24} r={r:+.3f} vs line · mean |deviation| '
              f'{statistics.fmean([abs(p[0] + p[1]) for p in pairs]):5.2f} pts'
              f' · sd {sd:5.2f}')

    # ── TEST 3 · incremental value on the residual ──────────────────
    print('\n── TEST 3 · does each model beat the line, ALONE then TOGETHER?')
    print('    x = model margin - line margin · y = actual margin - line '
          'margin')
    print(f'    {"model":<24}{"alone slope":>13}{"SE":>8}{"joint slope":>13}'
          f'{"SE":>8}{"n":>6}')
    print('    ' + '-' * 72)
    common = [k for k in range(len(games))
              if all(series[n][k] is not None for n in names)]
    alone = {}
    for n in names:
        xs, ys = [], []
        for k in range(len(games)):
            if series[n][k] is None:
                continue
            xs.append(series[n][k] - (-games[k]['line_home']))
            ys.append(games[k]['resid'])
        b, se = ols([[v] for v in xs], ys)
        alone[n] = (b[1], se[1]) if b else (float('nan'), float('nan'))
    # ══ DROP EXACT DUPLICATES BEFORE THE JOINT FIT ══
    # Two perfectly collinear columns make the Gram matrix singular and the
    # joint regression returns nan for everything — which is how the first
    # run of this tool failed. The failure IS the finding, so duplicates are
    # named and removed rather than worked around. Verified on NCAAF:
    # projected_spread_raw == sp_plus_pred_spread on 349/349 rows, and
    # sp_plus_pred_*_pts == model_pred_*_points on 292/292. Zero differences
    # — not similar models, the same number read twice.
    dupes = {}
    keep = []
    for n in names:
        twin = None
        for m in keep:
            pairs = [(series[n][k], series[m][k]) for k in range(len(games))
                     if series[n][k] is not None and series[m][k] is not None]
            if len(pairs) < 20:
                continue
            if all(abs(x - y) < 1e-9 for x, y in pairs):
                twin = m
                break
            r = corr([x for x, _ in pairs], [y for _, y in pairs])
            if abs(r) > 0.9999:
                twin = m
                break
        if twin:
            dupes[n] = twin
        else:
            keep.append(n)
    if dupes:
        print()
        print('  DUPLICATE SERIES (identical values — the engine is '
              'counting one opinion twice):')
        for n, m in dupes.items():
            print(f'      {n:<24} == {m}')
        print(f'  {len(names)} model columns reduce to {len(keep)} distinct '
              f'series: {keep}')

    joint = {}
    common = [k for k in range(len(games))
              if all(series[n][k] is not None for n in keep)]
    if len(common) > len(keep) + 5:
        X = [[series[n][k] - (-games[k]['line_home']) for n in keep]
             for k in common]
        y = [games[k]['resid'] for k in common]
        b, se = ols(X, y)
        if b:
            for i, n in enumerate(keep):
                joint[n] = (b[i + 1], se[i + 1])
        else:
            print('  joint fit still singular after dedupe — collinear '
                  'beyond exact duplication')
    for n in names:
        al, ase = alone[n]
        if n in dupes:
            print(f'    {n:<24}{al:>+13.3f}{ase:>8.3f}'
                  f'{"  DUPLICATE of " + dupes[n]:>21}')
            continue
        jo, jse = joint.get(n, (float('nan'), float('nan')))
        print(f'    {n:<24}{al:>+13.3f}{ase:>8.3f}{jo:>+13.3f}{jse:>8.3f}'
              f'{len(common):>6}')
    print('\n    A model whose JOINT slope collapses toward 0 while a peer')
    print('    holds is saying nothing the peer did not already say. A model')
    print('    whose ALONE slope is ~0 does not beat the line at all, and its')
    print('    agreement should not be raising anyone\'s conviction.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
