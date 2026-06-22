"""Phase 3 — per-prop signal weight rebalance backtest.

For each prop type:
  1. Pull 90d of graded picks with signals
  2. Build a binary feature matrix (signal_present[i] for each unique key)
  3. Fit a logistic regression with L2 regularization (small alpha to avoid
     overfit on rare signals)
  4. Predict probability per pick
  5. Compute:
     a. Current tier hit rate (baseline)
     b. New tier hit rate by probability percentile (top 20% = PRIME,
        20-50% = STRONG, 50-75% = LEAN, bottom 25% = SKIP)
  6. Report whether rebalancing would improve per-tier hit rates
  7. Per-prop verdict: WOULD_HELP / NO_CHANGE / WOULD_HURT

User direction: backtest-first, no hunches. This script ONLY reports —
it does NOT change any production code. Implementation is a separate
ship if the data supports it.
"""
import os
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

PROP_TYPES = [
    'ks_over', 'ks_under', 'bb_over', 'bb_under',
    'ha_over', 'ha_under', 'outs_over', 'outs_under',
    'er_over', 'er_under', 'hits_over', 'hits_under',
]

MIN_TOTAL_N = 60      # at least 60 graded picks to attempt rebalance
MIN_SIGNAL_N = 10     # signal must fire on at least 10 picks to count
HOLDOUT_FRAC = 0.30   # 30% holdout for validation
RNG_SEED = 42

# Skip internal signal keys (computed values, not predictors)
SKIP_KEYS = {
    '_book_line', '_edge_at_book', '_projected_bb', '_projected_ks',
    '_projected_outs', '_projected_hits', '_projected_er',
    '_display_label', '_pre_recal_tier', '_recal_multiplier',
    '_pre_recal_conviction', '_internal_suggested_line',
    '_starter_pen_relievers_3d', 'book_recalibration',
    '_shadow_conviction_if_applied', '_shadow_framing_delta',
    '_shadow_framing_value',
}


def pull(prop_type, days=90):
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_pipeline_props?game_date=gte.{since}'
            f'&prop_type=eq.{prop_type}'
            f'&select=tier,conviction,result,signals'
            f'&order=game_date.asc&limit=1000&offset={offset}',
            headers=H, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return [p for p in rows if isinstance(p, dict) and p.get('result') in ('Win', 'Loss')]


def extract_features(rows):
    """Build a binary feature matrix (n_picks × n_unique_signal_keys).

    Returns (X, y, feature_names, conviction_old, tier_old)."""
    # Collect all unique signal keys with sufficient count
    sig_count = defaultdict(int)
    for r in rows:
        sigs = r.get('signals') or {}
        if not isinstance(sigs, dict):
            continue
        for k in sigs.keys():
            if k in SKIP_KEYS:
                continue
            sig_count[k] += 1
    feature_names = sorted([k for k, c in sig_count.items() if c >= MIN_SIGNAL_N])
    n = len(rows)
    p = len(feature_names)
    X = np.zeros((n, p), dtype=int)
    y = np.zeros(n, dtype=int)
    conviction_old = np.zeros(n, dtype=float)
    tier_old = []
    for i, r in enumerate(rows):
        sigs = r.get('signals') or {}
        if isinstance(sigs, dict):
            for j, k in enumerate(feature_names):
                if k in sigs:
                    X[i, j] = 1
        y[i] = 1 if r['result'] == 'Win' else 0
        conviction_old[i] = r.get('conviction') or 0
        tier_old.append(r.get('tier', '?'))
    return X, y, feature_names, conviction_old, tier_old


def tier_from_percentile(probs):
    """Map each prediction to a tier based on its percentile rank.
    PRIME = top 20%, STRONG = 20-50%, LEAN = 50-75%, SKIP = bottom 25%."""
    rank = pd.Series(probs).rank(pct=True).values
    tiers = []
    for rk in rank:
        if rk >= 0.80: tiers.append('PRIME')
        elif rk >= 0.50: tiers.append('STRONG')
        elif rk >= 0.25: tiers.append('LEAN')
        else: tiers.append('SKIP')
    return tiers


def audit_prop(prop_type):
    rows = pull(prop_type, days=90)
    if len(rows) < MIN_TOTAL_N:
        return {'prop_type': prop_type, 'verdict': 'TOO_THIN', 'n': len(rows)}

    X, y, features, conv_old, tier_old = extract_features(rows)
    if len(features) < 2:
        return {'prop_type': prop_type, 'verdict': 'NO_FEATURES', 'n': len(rows)}

    # Time-ordered split (we already sorted by date in pull)
    split = int(len(rows) * (1 - HOLDOUT_FRAC))
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    conv_test = conv_old[split:]
    tier_test_old = tier_old[split:]

    # Logistic regression with mild L2 (handles rare-signal-collinearity)
    clf = LogisticRegression(
        C=1.0, penalty='l2', solver='lbfgs', max_iter=1000,
        random_state=RNG_SEED,
    )
    clf.fit(X_train, y_train)
    proba_test = clf.predict_proba(X_test)[:, 1]

    # Old tier hit rates (using stored tier label)
    old_tier_stats = defaultdict(lambda: [0, 0])
    for t, w in zip(tier_test_old, y_test):
        old_tier_stats[t][0 if w else 1] += 1

    # New tier hit rates (using percentile of predicted probability)
    new_tiers = tier_from_percentile(proba_test)
    new_tier_stats = defaultdict(lambda: [0, 0])
    for t, w in zip(new_tiers, y_test):
        new_tier_stats[t][0 if w else 1] += 1

    # Per-feature coefficients (sorted by magnitude)
    coefs = sorted(zip(features, clf.coef_[0].tolist()), key=lambda x: -abs(x[1]))

    # Compare PRIME hit rate old vs new
    old_prime = old_tier_stats.get('PRIME', [0, 0])
    new_prime = new_tier_stats.get('PRIME', [0, 0])
    old_prime_rate = old_prime[0] / max(1, sum(old_prime))
    new_prime_rate = new_prime[0] / max(1, sum(new_prime))

    # Verdict: WOULD_HELP only if new PRIME hit rate > old by at least 3pt
    # AND new PRIME sample >= 10 (so we're not just lucky)
    if sum(new_prime) >= 10 and (new_prime_rate - old_prime_rate) >= 0.03:
        verdict = 'WOULD_HELP'
    elif (new_prime_rate - old_prime_rate) <= -0.05:
        verdict = 'WOULD_HURT'
    else:
        verdict = 'NO_CHANGE'

    return {
        'prop_type': prop_type,
        'n_total': len(rows),
        'n_train': len(X_train),
        'n_test': len(X_test),
        'n_features': len(features),
        'old_tier_stats': dict(old_tier_stats),
        'new_tier_stats': dict(new_tier_stats),
        'top_coefs': coefs[:8],
        'verdict': verdict,
        'old_prime_rate': old_prime_rate,
        'new_prime_rate': new_prime_rate,
        'holdout_logloss': log_loss(y_test, proba_test, labels=[0, 1]),
        'holdout_acc': accuracy_score(y_test, (proba_test >= 0.5).astype(int)),
    }


def fmt_tier(stats):
    w, l = stats
    n = w + l
    if n == 0: return f'0-0'
    return f'{w}-{l} ({100*w/n:.0f}%, n={n})'


def main():
    print('=' * 100)
    print('PHASE 3 — Per-prop signal weight rebalance backtest')
    print('=' * 100)
    print(f'Method: logistic regression on signal flags, 70/30 time-ordered split')
    print(f'Ship gate: new PRIME hit rate must beat old by >=3pt with n>=10')
    print()

    results = []
    for pt in PROP_TYPES:
        r = audit_prop(pt)
        results.append(r)
        if r.get('verdict') in ('TOO_THIN', 'NO_FEATURES'):
            print(f'{pt:>12s}: SKIP — {r["verdict"]} (n={r.get("n", 0)})')
            continue
        print(f'\n{"─" * 100}')
        print(f'{pt.upper():>12s}  n_total={r["n_total"]}  n_holdout={r["n_test"]}  features={r["n_features"]}')
        print(f'{"─" * 100}')
        # Tier comparison
        for tier in ('PRIME', 'STRONG', 'LEAN', 'SKIP'):
            old_s = r['old_tier_stats'].get(tier, [0, 0])
            new_s = r['new_tier_stats'].get(tier, [0, 0])
            print(f'  {tier:>6s}: old {fmt_tier(old_s):>20s}  →  new {fmt_tier(new_s):>20s}')
        # Top coefficients (predictors model learned)
        print(f'  TOP COEFS (positive=predicts WIN, negative=predicts LOSS):')
        for fname, coef in r['top_coefs']:
            sign = '+' if coef >= 0 else '-'
            print(f'    {fname:>32s}: {coef:+.3f} {sign}')
        # Verdict
        delta_pp = (r['new_prime_rate'] - r['old_prime_rate']) * 100
        print(f'  VERDICT: {r["verdict"]} (PRIME old {r["old_prime_rate"]*100:.1f}% → new {r["new_prime_rate"]*100:.1f}%, Δ {delta_pp:+.1f}pt)')
        print(f'  Holdout: logloss {r["holdout_logloss"]:.3f}, acc {r["holdout_acc"]*100:.0f}%')

    print(f'\n\n{"=" * 100}')
    print('SUMMARY')
    print('=' * 100)
    print(f'{"prop":>14s}  {"verdict":>14s}  {"old PRIME":>12s}  {"new PRIME":>12s}  {"Δ":>6s}  {"n":>5s}')
    print('-' * 78)
    for r in results:
        if r.get('verdict') in ('TOO_THIN', 'NO_FEATURES'):
            print(f'  {r["prop_type"]:>12s}  {r["verdict"]:>12s}  ' + '-' * 32 + f'  n={r.get("n", 0)}')
            continue
        delta = (r['new_prime_rate'] - r['old_prime_rate']) * 100
        print(f'  {r["prop_type"]:>12s}  {r["verdict"]:>14s}  '
              f'{r["old_prime_rate"]*100:>10.1f}%  {r["new_prime_rate"]*100:>10.1f}%  '
              f'{delta:>+5.1f}pt  {r["n_total"]:>5d}')


if __name__ == '__main__':
    main()
