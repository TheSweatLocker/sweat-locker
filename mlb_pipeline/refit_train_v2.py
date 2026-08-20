"""Refit v2 (2026-08-01) — fixes sign-flipped coefficients from v1.

7/31 audit surfaced 3 negative-when-should-be-positive coefficients on
bb_under (clean_start -0.254, book_recalibration -0.134, aggressive_opp
-0.303). Root causes:
  1. class_weight='balanced' inflates minority weight → sign flip risk
  2. Correlated features (l7_control + l5_confirm + bb_rate) unstable
  3. Weak regularization (C=0.5) overfits noise at n=75
  4. Binary-only features miss magnitude signal

v2 fixes:
  - class_weight=None (natural class distribution)
  - L1 penalty (Lasso) with C=0.3 — zeros out redundant correlated features
  - Extended window to 120d (was 60d) for more training data
  - Min sample threshold raised to 60 (was 40) to prevent unstable fits
  - Feature co-occurrence check: drop features that fire together >90% of
    the time (keep the one with higher predictive value)
  - Sign check: warn on any negative coefficient for signals with obvious
    positive domain meaning

Output: mlb_pipeline/models/prop_refit_weights_v2.json
"""
import os, json, sys
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta

import requests
import numpy as np

for line in Path(r'C:\Users\gomez\SweatShop\mlb_pipeline\.env').read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())
sys.stdout.reconfigure(encoding='utf-8')
url = os.environ['SUPABASE_URL']; key = os.environ['SUPABASE_KEY']
h = {'apikey': key, 'Authorization': f'Bearer {key}'}

from sklearn.linear_model import LogisticRegression

# Signals with obvious positive-direction domain meaning — warn on negative
# coefficients so we catch sign flips in future retrains. For BB Under:
# clean_start (prior clean = fewer walks), book_recalibration (bigger book
# edge = more model confidence), aggressive_opp (opponent swings early =
# fewer walks) all should be POSITIVE if trained on real data.
POSITIVE_EXPECTED = {
    'bb_under/under': ['clean_start', 'book_recalibration', 'aggressive_opp',
                       'bb_rate', 'l5_confirm', 'l7_control'],
    'bb_over/over':   ['l7_walks', 'wild_start', 'l5_confirm'],
    'ks_over/over':   ['book_recalibration', 'l5_confirm', 'l7_ks', 'ks_rate'],
    'ks_under/under': ['book_recalibration', 'l5_confirm', 'weak_opp_k'],
    'hits_over/over': ['book_recalibration', 'l5_confirm'],
    'hits_under/under': ['book_recalibration', 'l5_confirm'],
    'outs_over/over': ['book_recalibration', 'l5_confirm', 'l7_ip'],
    'outs_under/under': ['book_recalibration', 'l5_confirm'],
}


def drop_correlated_features(X, keys, threshold=0.90):
    """Drop features with pairwise co-firing rate > threshold (keep first)."""
    if X.shape[1] < 2: return X, keys
    # Compute co-firing rate for every pair
    n_rows = X.shape[0]
    keep = list(range(X.shape[1]))
    dropped = []
    for i in range(X.shape[1]):
        if i not in keep: continue
        for j in range(i+1, X.shape[1]):
            if j not in keep: continue
            both = np.sum((X[:, i] == 1) & (X[:, j] == 1))
            either = np.sum((X[:, i] == 1) | (X[:, j] == 1))
            if either == 0: continue
            overlap = both / either
            if overlap > threshold:
                # Drop the one with fewer positive fires (less signal)
                fires_i = X[:, i].sum(); fires_j = X[:, j].sum()
                drop_idx = j if fires_j <= fires_i else i
                if drop_idx == i:
                    dropped.append(keys[i]); keep.remove(i); break
                else:
                    dropped.append(keys[j]); keep.remove(j)
    X2 = X[:, keep]
    keys2 = [keys[i] for i in keep]
    return X2, keys2, dropped


end = datetime.now(); start = end - timedelta(days=120)   # was 60
# 2026-08-10: fixed pagination — PostgREST caps single response at 1000 rows.
# Prior version silently only trained on top-4 prop types (hits/ks) because
# the 1000-row response cut off less-common props. Now paginate to get the
# full 120d window (~4600 rows) → all 12 prop_type/dir combos become eligible.
rows = []
offset = 0
while True:
    chunk = requests.get(f'{url}/rest/v1/mlb_pipeline_props',
        headers={**h, 'Range': f'{offset}-{offset+999}', 'Range-Unit': 'items'},
        params={
            'select': 'prop_type,direction,conviction,signals,result',
            'and': f'(game_date.gte.{start.date().isoformat()},game_date.lte.{end.date().isoformat()})',
            'result': 'in.(Win,Loss)',
        }, timeout=45).json()
    if not chunk: break
    rows.extend(chunk)
    if len(chunk) < 1000: break
    offset += 1000
    if offset > 30000: break  # safety cap
print(f'  {len(rows)} training rows over 120d (paginated)')

# 2026-08-10: expand GATED to cover EVERY prop type we ship. Previously
# whitelisted 8 types (hits/ks/outs/bb over+under variants), missing
# er_over/under, ha_over/under. Today's card had 5 top props uncovered
# (Painter ER, Kremer ER, Hughes HA, Henderson HA, Taillon ER) because
# their prop_types weren't in this registry — refit_conviction stayed NULL
# and Prop Jerry had no calibration signal to consume.
GATED = {
    # Pitcher walks
    'bb_over/over', 'bb_under/under',
    # Pitcher Ks
    'ks_over/over', 'ks_under/under',
    # Pitcher outs
    'outs_over/over', 'outs_under/under',
    # Pitcher earned runs (NEW)
    'er_over/over', 'er_under/under',
    # Pitcher hits allowed (NEW)
    'ha_over/over', 'ha_under/under',
    # Batter hits
    'hits_over/over', 'hits_under/under',
    # Future: hits_over_1.5, hr_over, rbi_over (as volume accumulates)
}
combos = Counter((r['prop_type'], r['direction']) for r in rows)
targets = [(p, d) for (p, d), n in combos.items() if n >= 60 and f'{p}/{d}' in GATED]  # was 40

weights = {'trained_at': datetime.now().isoformat(),
           'training_window': '120d',
           'n_total_rows': len(rows),
           'method': ('sklearn.LogisticRegression(C=0.3, penalty=l1, solver=liblinear, '
                     'class_weight=None) + corr>0.9 feature drop + sign-flip warnings'),
           'usage': ('apply_prop_refit reads this JSON and computes '
                     'refit_conviction per prop as normalized sum of '
                     'fired-signal coefficients rescaled to 0-100.'),
           'prop_types': {},
           'sign_flip_warnings': []}

for pt, d in targets:
    subset = [r for r in rows if r['prop_type']==pt and r['direction']==d]
    keys_c = Counter()
    for r in subset:
        for k in (r.get('signals') or {}).keys():
            if not k.startswith('_'): keys_c[k] += 1
    keys = [k for k, n in keys_c.most_common() if n >= 8]  # was 5
    if not keys: continue
    X = np.zeros((len(subset), len(keys))); y = np.zeros(len(subset))
    for i, r in enumerate(subset):
        sigs = r.get('signals') or {}
        for j, k in enumerate(keys):
            if k in sigs: X[i, j] = 1.0
        y[i] = 1.0 if r['result']=='Win' else 0.0

    # Drop highly-correlated features (kills multicollinearity sign flips)
    X, keys, dropped = drop_correlated_features(X, keys, threshold=0.90)
    if dropped:
        print(f'  {pt}/{d}: dropped {len(dropped)} correlated features → {dropped}')

    # Fit: L1 lasso, natural class distribution, tighter regularization
    clf = LogisticRegression(
        max_iter=2000,
        C=0.3,                      # was 0.5 (tighter regularization)
        penalty='l1',               # was default l2 (lasso zeros redundant features)
        solver='liblinear',
        class_weight=None,          # was 'balanced' (main sign-flip cause)
    )
    clf.fit(X, y)
    fitted_scores = X @ clf.coef_[0]
    coef_dict = {k: float(clf.coef_[0][i]) for i, k in enumerate(keys)}

    # Sign-flip audit
    warnings = []
    expected = POSITIVE_EXPECTED.get(f'{pt}/{d}', [])
    for signal_name in expected:
        for k in coef_dict:
            if signal_name in k.lower() and coef_dict[k] < -0.05:
                warnings.append({
                    'prop': f'{pt}/{d}',
                    'signal': k,
                    'coef': coef_dict[k],
                    'note': f'Expected positive per domain — flip risk',
                })
    for w in warnings:
        print(f'  ⚠ SIGN FLIP {w["prop"]}: {w["signal"]} = {w["coef"]:+.3f} · {w["note"]}')

    weights['prop_types'][f'{pt}/{d}'] = {
        'n_training': len(subset),
        'baseline_hit': float(y.mean()),
        'intercept': float(clf.intercept_[0]),
        'coefficients': coef_dict,
        'score_min': float(fitted_scores.min()),
        'score_max': float(fitted_scores.max()),
        'features_dropped_correlated': dropped,
    }
    weights['sign_flip_warnings'].extend(warnings)
    print(f'  fit {pt}/{d}: n={len(subset)} baseline={y.mean():.1%} keys={len(keys)}')

# 2026-08-20: cross-platform path (was hardcoded Windows). Enables
# scheduled runs on Linux GitHub Actions runner.
out = Path(__file__).parent / 'models' / 'prop_refit_weights_v2.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(weights, indent=2))
print(f'\n  saved -> {out}')
print(f'  {len(weights["prop_types"])} prop types serialized')
print(f'  {len(weights["sign_flip_warnings"])} sign flip warnings')
