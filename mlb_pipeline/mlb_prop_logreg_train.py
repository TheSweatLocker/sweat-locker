"""MLB PROP logistic regression trainer — same solve-for-x approach as
mlb_ml_logreg_train.py, applied per-prop-type.

Per user directive 2026-09-03: 'take the same approach for prop pipeline'.
Fits a separate logistic regression per prop_type (or a unified model
with prop_type dummies) predicting Win/Loss from the numeric features
available on each prop row.

Features used:
  - book_over_odds, book_under_odds  (market juice)
  - book_line, prop_line             (posted line)
  - conviction, refit_conviction     (our current scorer output — LR
                                       will learn true weight)
  - player_l5_hit_count, player_l10_hit_count  (recent form momentum)
  - player_season_hit_pct            (season-long baseline)
  - stack_alert (0/1)                (correlated-stack flag)
  - prop_type_ONEHOT                 (per-market bias)
  - direction_over (0/1)             (over vs under bias)

Target: result == 'Win' (1) vs 'Loss' (0). Void/Push excluded.

Model persisted to models/mlb_prop_logreg.json.

USAGE:
    python mlb_prop_logreg_train.py                # 90d train
    python mlb_prop_logreg_train.py --days 120
    python mlb_prop_logreg_train.py --dry-run
"""
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL']; K = os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': f'Bearer {K}'}
MODELS_DIR = Path(__file__).parent / 'models'
MODELS_DIR.mkdir(exist_ok=True)

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

NUMERIC_FEATURES = [
    'book_over_odds', 'book_under_odds', 'book_line', 'prop_line',
    'conviction', 'refit_conviction',
    'player_l5_hit_count', 'player_l10_hit_count',
    'player_season_hit_pct',
]
# We add prop_type one-hot + direction_over binary + stack_alert binary
# as additional features. See build_dataset() below.

PROP_TYPES = ['hits_over', 'hits_under', 'ks_over', 'ks_under',
              'bb_over', 'bb_under', 'ha_over', 'ha_under',
              'outs_over', 'outs_under', 'er_over', 'er_under']


def _to_float(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def train(days: int = 90, dry_run: bool = False):
    print(f'== MLB prop logreg train · {days}d ==')
    d_start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    d_end   = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    rows = []
    for off in range(0, 20000, 1000):
        r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
            params={'select': 'prop_type,direction,book_over_odds,book_under_odds,book_line,'
                              'prop_line,conviction,refit_conviction,player_l5_hit_count,'
                              'player_l10_hit_count,player_season_hit_pct,stack_alert,result',
                    'game_date': f'gte.{d_start}', 'resolved_at': 'not.is.null',
                    'result': 'in.(Win,Loss)',
                    'limit': 1000, 'offset': off, 'order': 'game_date.asc'},
            headers=H, timeout=30)
        chunk = r.json() if isinstance(r.json(), list) else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
    print(f'  pulled {len(rows)} resolved props (Win/Loss only)')
    if len(rows) < 300:
        print('  ⚠ insufficient data (<300 props) — aborting'); return

    # Build feature matrix
    # Columns: NUMERIC_FEATURES + [direction_over, stack_alert_bool] + prop_type_ONEHOT
    all_features = list(NUMERIC_FEATURES) + ['direction_over', 'stack_alert_bool']
    all_features += [f'ptype_{pt}' for pt in PROP_TYPES]

    X_rows, y_rows = [], []
    for r in rows:
        pt = r.get('prop_type') or ''
        if pt not in PROP_TYPES: continue  # skip malformed prop_types
        direction = (r.get('direction') or '').lower()
        stack = 1.0 if r.get('stack_alert') else 0.0
        row = []
        for f in NUMERIC_FEATURES:
            v = _to_float(r.get(f))
            row.append(float('nan') if v is None else v)
        row.append(1.0 if direction == 'over' else 0.0)
        row.append(stack)
        for onehot_pt in PROP_TYPES:
            row.append(1.0 if pt == onehot_pt else 0.0)
        X_rows.append(row)
        y_rows.append(1 if r.get('result') == 'Win' else 0)
    X = np.array(X_rows); y = np.array(y_rows)
    print(f'  X shape: {X.shape}, base win rate: {y.mean()*100:.1f}%')

    # Drop all-NaN columns (rare — mostly one-hots)
    valid_col_mask = ~np.all(np.isnan(X), axis=0)
    dropped = [f for f, keep in zip(all_features, valid_col_mask) if not keep]
    if dropped: print(f'  ⚠ dropping all-NaN features: {dropped}')
    all_features = [f for f, keep in zip(all_features, valid_col_mask) if keep]
    X = X[:, valid_col_mask]

    imputer = SimpleImputer(strategy='median')
    X_imp = imputer.fit_transform(X)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_imp)

    # Train/test split 70/30
    rng = np.random.RandomState(42)
    idx = rng.permutation(len(y))
    X_scaled = X_scaled[idx]; y = y[idx]
    cut = int(len(y) * 0.7)

    lr = LogisticRegression(max_iter=1000, C=1.0)
    lr.fit(X_scaled[:cut], y[:cut])
    train_acc = lr.score(X_scaled[:cut], y[:cut])
    test_acc  = lr.score(X_scaled[cut:], y[cut:])
    baseline  = y[cut:].mean()
    print(f'\n  Train acc: {train_acc*100:.1f}%   Test acc: {test_acc*100:.1f}%')
    print(f'  Baseline (always-win holdout): {baseline*100:.1f}%')
    print(f'  Lift over baseline: {(test_acc - baseline)*100:+.1f}pp')

    # Tiered predictions on holdout
    probs = lr.predict_proba(X_scaled[cut:])[:, 1]
    print(f'\n  Tiered predictions on holdout (p_hit):')
    for lo, hi, tier in [(0.70, 1.01, 'PRIME'), (0.60, 0.70, 'STRONG'),
                         (0.50, 0.60, 'LEAN'), (0.40, 0.50, 'COIN'),
                         (0.00, 0.40, 'FADE')]:
        mask = (probs >= lo) & (probs < hi)
        n = int(mask.sum())
        if n == 0: continue
        wins = int(((y[cut:] == 1) & mask).sum())
        hit = 100*wins/n if n else 0
        print(f'    {tier:6s} (p={lo:.2f}-{hi:.2f}): {wins}/{n} = {hit:.1f}%')

    # Top features by |coefficient|
    coefs_ranked = sorted(zip(all_features, lr.coef_[0]), key=lambda x: -abs(x[1]))
    print(f'\n  Top 12 features by |coefficient|:')
    for f, c in coefs_ranked[:12]:
        dir_txt = 'BOOSTS win' if c > 0 else 'reduces win'
        print(f'    {f:30s}  coef={c:>+6.3f}  ({dir_txt})')

    # Refit on full data
    lr_full = LogisticRegression(max_iter=1000, C=1.0)
    lr_full.fit(X_scaled, y)

    # Save
    model_out = {
        'version': datetime.now(timezone.utc).isoformat(),
        'features': all_features,
        'prop_types_onehot': PROP_TYPES,
        'coefficients': lr_full.coef_[0].tolist(),
        'intercept': float(lr_full.intercept_[0]),
        'imputer_medians': imputer.statistics_.tolist(),
        'scaler_mean': scaler.mean_.tolist(),
        'scaler_scale': scaler.scale_.tolist(),
        'meta': {
            'train_days': days,
            'n_train': int(cut),
            'n_test': int(len(y) - cut),
            'test_accuracy': float(test_acc),
            'baseline_accuracy': float(baseline),
            'lift_pp': float((test_acc - baseline) * 100),
        },
    }
    out_path = MODELS_DIR / 'mlb_prop_logreg.json'
    if dry_run:
        print(f'\n  [DRY] would write {out_path}')
    else:
        out_path.write_text(json.dumps(model_out, indent=2))
        print(f'\n  ✓ saved {out_path} ({len(all_features)} features, test_acc={test_acc*100:.1f}%)')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=90)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    train(days=args.days, dry_run=args.dry_run)
