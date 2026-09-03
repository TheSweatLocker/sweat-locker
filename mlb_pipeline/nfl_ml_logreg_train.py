"""NFL ML logistic regression trainer — mirrors mlb_ml_logreg_train.py
pattern for NFL market. Applied to nfl_game_results (~1000 games across
4 seasons 2022-2025).

Features are thinner than MLB (no rich advanced stats stored in results
table), but market-embedded lines + rest days + div-game flag give the
model real signal.

USAGE:
    python nfl_ml_logreg_train.py                # all seasons train
    python nfl_ml_logreg_train.py --dry-run
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

FEATURE_CANDIDATES = [
    'close_spread', 'open_spread', 'close_total', 'open_total',
    'close_home_ml', 'close_away_ml', 'open_home_ml', 'open_away_ml',
    'home_rest', 'away_rest',
    'div_game',
    # 'overtime' EXCLUDED — post-game field, data leakage
]


def _probe_valid_cols() -> set:
    r = requests.get(f'{SB}/rest/v1/nfl_game_results?select=*&limit=1', headers=H, timeout=10)
    if r.status_code != 200 or not r.json(): return set()
    return set(r.json()[0].keys())


def _to_float(v):
    if isinstance(v, bool): return 1.0 if v else 0.0
    try: return float(v)
    except (TypeError, ValueError): return None


def train(dry_run: bool = False):
    print(f'== NFL ML logreg train ==')
    valid_cols = _probe_valid_cols()
    features = [c for c in FEATURE_CANDIDATES if c in valid_cols]
    print(f'  {len(features)}/{len(FEATURE_CANDIDATES)} features exist')

    select_str = 'game_id,game_date,home_win,' + ','.join(features)
    rows = []
    for off in range(0, 5000, 500):
        r = requests.get(f'{SB}/rest/v1/nfl_game_results',
            params={'select': select_str, 'home_score': 'not.is.null',
                    'home_win': 'not.is.null',
                    'limit': 500, 'offset': off, 'order': 'game_date.asc'},
            headers=H, timeout=30)
        chunk = r.json() if isinstance(r.json(), list) else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 500: break
    print(f'  pulled {len(rows)} resolved NFL games')
    if len(rows) < 300:
        print('  ⚠ insufficient data (<300 games) — aborting'); return

    X_rows, y_rows = [], []
    for r in rows:
        y = r.get('home_win')
        if y is None: continue
        row_vals = [float('nan') if _to_float(r.get(f)) is None else _to_float(r.get(f))
                    for f in features]
        X_rows.append(row_vals)
        y_rows.append(int(bool(y)))
    X = np.array(X_rows); y = np.array(y_rows)
    print(f'  X shape: {X.shape}, home_win rate: {y.mean()*100:.1f}%')

    # Drop all-NaN columns
    valid_col_mask = ~np.all(np.isnan(X), axis=0)
    dropped = [f for f, keep in zip(features, valid_col_mask) if not keep]
    if dropped: print(f'  ⚠ dropping all-NaN features: {dropped}')
    features = [f for f, keep in zip(features, valid_col_mask) if keep]
    X = X[:, valid_col_mask]

    imputer = SimpleImputer(strategy='median')
    X_imp = imputer.fit_transform(X)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_imp)

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
    print(f'  Baseline: {baseline*100:.1f}%   Lift: {(test_acc - baseline)*100:+.1f}pp')

    probs = lr.predict_proba(X_scaled[cut:])[:, 1]
    print(f'\n  Tiered predictions on holdout:')
    for lo, hi, tier, side in [(0.65, 1.01, 'PRIME',   'HOME'),
                                (0.55, 0.65, 'STRONG', 'HOME'),
                                (0.45, 0.55, 'COIN',   'NONE'),
                                (0.35, 0.45, 'STRONG', 'AWAY'),
                                (0.00, 0.35, 'PRIME',  'AWAY')]:
        mask = (probs >= lo) & (probs < hi)
        n = int(mask.sum())
        if n == 0: continue
        if side == 'HOME': wins = int(((y[cut:] == 1) & mask).sum())
        elif side == 'AWAY': wins = int(((y[cut:] == 0) & mask).sum())
        else: wins = 0
        hit = 100*wins/n if n else 0
        print(f'    {tier}_{side:4s}: {wins}/{n} = {hit:.1f}%')

    # Top features
    print(f'\n  Top features by |coefficient|:')
    for f, c in sorted(zip(features, lr.coef_[0]), key=lambda x: -abs(x[1]))[:10]:
        print(f'    {f:20s} {c:>+7.3f}')

    lr_full = LogisticRegression(max_iter=1000, C=1.0)
    lr_full.fit(X_scaled, y)

    out = {
        'version': datetime.now(timezone.utc).isoformat(),
        'features': features,
        'coefficients': lr_full.coef_[0].tolist(),
        'intercept': float(lr_full.intercept_[0]),
        'imputer_medians': imputer.statistics_.tolist(),
        'scaler_mean': scaler.mean_.tolist(),
        'scaler_scale': scaler.scale_.tolist(),
        'meta': {
            'test_accuracy': float(test_acc),
            'baseline_accuracy': float(baseline),
            'lift_pp': float((test_acc - baseline) * 100),
            'n_train': int(cut), 'n_test': int(len(y) - cut),
        },
    }
    out_path = MODELS_DIR / 'nfl_ml_logreg.json'
    if dry_run:
        print(f'\n  [DRY] would write {out_path}')
    else:
        out_path.write_text(json.dumps(out, indent=2))
        print(f'\n  ✓ saved {out_path}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    train(dry_run=args.dry_run)
