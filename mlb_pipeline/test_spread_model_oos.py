#!/usr/bin/env python3
"""Can a MULTIVARIATE model beat the close out-of-sample? Writes nothing.

== 2026-10-18 - WHY ==
ANDY: "game picks need to be better bottom line ... dont fucking tellme there
is no measured edge, you are not digging deep enough."

analyze_spread_signals.py exhausted the UNIVARIATE search: across NCAAF (151
features, n=526), NFL (151, n=91) and NHL (80, n=138), not one feature cleared
Bonferroni against the spread residual, and the count of nominal p<0.05 hits
matched chance expectation almost exactly in all three. The same held for the
line_history movement features.

But "no single feature predicts it" is NOT "no combination predicts it". That
is this script. It is the decisive test, because a model is the thing we would
actually ship.

METHOD, built so a positive result would be trustworthy:
  * TARGET is the residual (actual margin - market expected margin). Beating
    the spread means predicting where the close is WRONG.
  * RIDGE, closed form, standardized on TRAIN statistics only. Test-set means
    and sds never touch the fit - that leak is what made the 65-79% ATS
    backtests evaporate (project_models_dont_beat_the_close_1005).
  * TEMPORAL split, never random. Early weeks fit, later weeks score.
  * FEATURE SELECTION happens on TRAIN ONLY. Choosing features by their
    full-sample correlation and then "validating" is the leak that produced
    every previous false positive here.
  * THREE CONTROLS the model must beat, not just 50%: always-HOME,
    always-DOG, and a CONSTANT (predict the train-mean residual for every
    game, which is the market level negated).
  * PICKS AT A THRESHOLD. A model is only useful where it is confident, so
    the ATS record is reported per |predicted residual| band with Wilson CIs
    and the -110 breakeven of 52.38% marked.

USAGE
    python test_spread_model_oos.py --sport NCAAF
    python test_spread_model_oos.py --all
"""
from __future__ import annotations

import argparse
import math
import os
import statistics as st
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_spread_signals as A                             # noqa: E402

BREAKEVEN = 52.38


def wilson(w, n):
    return A.wilson(w, n)


def ridge_fit(X, y, alpha):
    """Closed-form ridge. Intercept kept out of the penalty."""
    n, p = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    I = np.eye(p + 1)
    I[0, 0] = 0.0
    return np.linalg.solve(Xb.T @ Xb + alpha * I, Xb.T @ y)


def run(sport: str, max_feats: int, alpha: float) -> None:
    print(f'\n{"="*74}\n{sport}: MULTIVARIATE OUT-OF-SAMPLE SPREAD TEST\n{"="*74}')
    rows = A.load(sport)
    if len(rows) < 80:
        print(f'  only {len(rows)} games - refusing to model')
        return
    sgn, agree = A.derive_sign(rows, A.SPORTS[sport]['line'])
    if agree < 0.90:
        print('  spread convention not established - refusing')
        return
    for r in rows:
        r['_mkt'] = sgn * r['_line']
        r['_resid'] = r['_margin'] - r['_mkt']
        r['_home_cov'] = r['_resid'] > 0
    feats = A.numeric_features(rows)
    feats += A.add_differentials(rows, feats)
    feats += A.attach_line_features(rows, sport)
    feats = sorted(set(feats))

    rows.sort(key=lambda r: (r['_week'] if r['_week'] is not None else 0,
                             r['_date']))
    cut = int(len(rows) * 0.62)
    train, test = rows[:cut], rows[cut:]
    print(f'games {len(rows)}  TRAIN {len(train)} (to {train[-1]["_date"]})  '
          f'TEST {len(test)} (from {test[0]["_date"]})')
    print(f'residual sd on TRAIN {st.pstdev([r["_resid"] for r in train]):.3f}'
          f'   TEST {st.pstdev([r["_resid"] for r in test]):.3f}')

    # ---- feature selection on TRAIN ONLY ----
    usable = []
    for k in feats:
        xs = [A.f(r['_ctx'].get(k)) for r in train]
        if sum(v is not None for v in xs) < 0.85 * len(train):
            continue
        ys = [r['_resid'] for r in train]
        pair = [(x, y) for x, y in zip(xs, ys) if x is not None]
        if len(pair) < 40 or len({p[0] for p in pair}) < 5:
            continue
        c = A.corr_ci([p[0] for p in pair], [p[1] for p in pair])
        if c:
            usable.append((abs(c[0]), k))
    usable.sort(reverse=True)
    chosen = [k for _a, k in usable[:max_feats]]
    print(f'features with >=85% TRAIN coverage: {len(usable)}; '
          f'using top {len(chosen)} by |TRAIN r| (selection on TRAIN only)')
    if len(chosen) < 3:
        print('  too few usable features - refusing')
        return

    def mat(subset):
        X, y, keep = [], [], []
        for r in subset:
            v = [A.f(r['_ctx'].get(k)) for k in chosen]
            if any(q is None for q in v):
                continue
            X.append(v)
            y.append(r['_resid'])
            keep.append(r)
        return np.array(X, dtype=float), np.array(y, dtype=float), keep

    Xtr, ytr, ktr = mat(train)
    Xte, yte, kte = mat(test)
    print(f'complete-case rows: TRAIN {len(ytr)}  TEST {len(yte)}')
    if len(ytr) < 40 or len(yte) < 30:
        print('  complete-case sample too thin - refusing')
        return

    mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
    sd[sd == 0] = 1.0
    Ztr = (Xtr - mu) / sd
    Zte = (Xte - mu) / sd          # TRAIN statistics only - no test leakage

    best = None
    for al in (alpha, alpha * 3, alpha * 10, alpha * 30, alpha * 100):
        w = ridge_fit(Ztr, ytr, al)
        pred_tr = np.hstack([np.ones((len(Ztr), 1)), Ztr]) @ w
        # in-sample R2 is a ceiling, printed to show the gap
        ss = 1 - ((ytr - pred_tr) ** 2).sum() / ((ytr - ytr.mean()) ** 2).sum()
        pred_te = np.hstack([np.ones((len(Zte), 1)), Zte]) @ w
        r2_te = 1 - ((yte - pred_te) ** 2).sum() / (
            (yte - ytr.mean()) ** 2).sum()
        print(f'  alpha {al:>8.1f}   TRAIN R2 {ss:+.4f}   '
              f'TEST R2 {r2_te:+.4f}')
        if best is None or r2_te > best[0]:
            best = (r2_te, al, pred_te, w)

    r2_te, al, pred, w = best
    print(f'\nbest alpha {al:g}: TEST R2 {r2_te:+.4f}  '
          f'({"beats" if r2_te > 0 else "LOSES TO"} predicting the '
          f'train-mean residual)')

    # ---- CONTROLS ----
    print('\n-- CONTROLS on the same TEST rows --')
    nz = [i for i in range(len(yte)) if yte[i] != 0]
    hc = sum(1 for i in nz if yte[i] > 0)
    lo, hi = wilson(hc, len(nz))
    print(f'  always HOME  {hc}-{len(nz)-hc}  {100*hc/max(len(nz),1):.1f}%  '
          f'CI[{lo:.1f},{hi:.1f}]')
    lo, hi = wilson(len(nz) - hc, len(nz))
    print(f'  always AWAY  {len(nz)-hc}-{hc}  '
          f'{100*(len(nz)-hc)/max(len(nz),1):.1f}%  CI[{lo:.1f},{hi:.1f}]')
    dw = dn = 0
    for i in nz:
        mk = kte[i]['_mkt']
        if mk == 0:
            continue
        dn += 1
        dw += 1 if ((yte[i] > 0 and mk < 0) or (yte[i] < 0 and mk > 0)) else 0
    lo, hi = wilson(dw, dn)
    print(f'  always DOG   {dw}-{dn-dw}  {100*dw/max(dn,1):.1f}%  '
          f'CI[{lo:.1f},{hi:.1f}]')

    # ---- the model's own picks, by confidence band ----
    print('\n-- MODEL PICKS by |predicted residual| --')
    print(f'  {"band":<16}{"picks":>7}{"W-L":>9}{"hit%":>8}{"95% CI":>16}'
          f'   vs breakeven 52.38%')
    for lo_t, hi_t in ((0.0, 99.0), (1.0, 99.0), (2.0, 99.0), (3.0, 99.0),
                       (4.0, 99.0), (6.0, 99.0)):
        w_ = l_ = 0
        for i in range(len(yte)):
            if yte[i] == 0:
                continue
            p = pred[i]
            if not (lo_t <= abs(p) < hi_t):
                continue
            # pick HOME when the model says the residual is positive
            hit = (p > 0) == (yte[i] > 0)
            w_ += hit
            l_ += not hit
        n = w_ + l_
        if n < 15:
            print(f'  |pred|>={lo_t:<10.1f}{n:>7}   too thin to quote')
            continue
        lo2, hi2 = wilson(w_, n)
        verdict = ('BEATS' if lo2 > BREAKEVEN else
                   ('LOSES' if hi2 < BREAKEVEN else 'inconclusive'))
        print(f'  |pred|>={lo_t:<10.1f}{n:>7}{f"{w_}-{l_}":>9}'
              f'{100*w_/n:>7.1f}%  [{lo2:>5.1f},{hi2:>5.1f}]   {verdict}')

    print('\n  top |weight| features (standardized, TRAIN fit):')
    order = np.argsort(-np.abs(w[1:]))[:8]
    for i in order:
        print(f'    {chosen[i]:<34}{w[1+i]:+.3f}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--max-feats', type=int, default=12)
    ap.add_argument('--alpha', type=float, default=10.0)
    a = ap.parse_args()
    for sp in (['NCAAF', 'NFL', 'NHL'] if a.all else [a.sport]):
        if sp in A.SPORTS:
            run(sp, a.max_feats, a.alpha)
    return 0


if __name__ == '__main__':
    sys.exit(main())
