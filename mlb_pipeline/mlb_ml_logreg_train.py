"""MLB ML logistic regression trainer — solve-for-x approach.

Trained on 90d of resolved games via mlb_game_results. Discovers feature
weights that predict home_win, saves model to models/mlb_ml_logreg.json
for the prediction path (mlb_ml_logreg_predict.py) to load.

USER FRAMING (2026-09-03): 'if we know Y and X, solve for how they
connect.' Test-set accuracy on 90d: 64.0% vs 52.5% always-home baseline
= +11.5pp lift. PRIME_HOME confidence bucket (p>=0.65) hits 71.6% n=95
compared to current ensemble PRIME 12.5% n=16 on same holdout.

USAGE:
    python mlb_ml_logreg_train.py                # 90d train, save model
    python mlb_ml_logreg_train.py --days 120     # extend window
    python mlb_ml_logreg_train.py --dry-run      # print, don't save
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

# Full feature candidate list — probed against live schema at load time.
FEATURE_CANDIDATES = [
    'close_spread', 'open_spread', 'close_total', 'open_total',
    'home_ml_close', 'away_ml_close', 'home_ml_open', 'away_ml_open',
    'home_sp_era', 'away_sp_era', 'home_sp_xera', 'away_sp_xera',
    'home_sp_k_pct', 'away_sp_k_pct', 'home_sp_whiff_rate', 'away_sp_whiff_rate',
    'home_sp_gb_pct', 'away_sp_gb_pct', 'home_sp_days_rest', 'away_sp_days_rest',
    'home_sp_last5_era', 'away_sp_last5_era',
    'home_runs_per_game', 'away_runs_per_game',
    'home_ops', 'away_ops', 'home_wrc_plus', 'away_wrc_plus',
    'home_team_k_pct', 'away_team_k_pct',
    'home_bullpen_era', 'away_bullpen_era',
    'park_run_factor', 'temperature', 'wind_mph',
    'projected_total', 'projected_spread',
    'jerry_pred_total', 'jerry_pred_spread',
    'jerry_pred_home_runs', 'jerry_pred_away_runs',
    'model_pred_home_runs', 'model_pred_away_runs',
    'model_pred_spread', 'model_pred_total',
    'signal_confluence_net',
    'home_offense_drift', 'away_offense_drift',
    'home_last5_run_diff', 'away_last5_run_diff',
    'home_last10_runs_per_game', 'away_last10_runs_per_game',
    'home_l10_wins', 'home_l10_losses', 'away_l10_wins', 'away_l10_losses',
    'home_wrc_vs_opp_hand', 'away_wrc_vs_opp_hand',
    'home_ops_vs_opp_hand', 'away_ops_vs_opp_hand',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_pitcher_vs_team_era', 'away_pitcher_vs_team_era',
    'home_pitcher_vs_team_recent_era', 'away_pitcher_vs_team_recent_era',
    'panel_implied_total', 'panel_implied_margin',
    'spread_delta',
    'home_team_oaa', 'away_team_oaa',
    'home_team_xwoba', 'away_team_xwoba',
    'home_team_barrel_pct', 'away_team_barrel_pct',
    'home_catcher_framing', 'away_catcher_framing',
    'home_ops_last7', 'away_ops_last7',
    'home_ops_last14', 'away_ops_last14',
    'home_wrc_proxy_l14', 'away_wrc_proxy_l14',
    'home_pitcher_projected_er', 'away_pitcher_projected_er',
    'home_pitcher_projected_outs', 'away_pitcher_projected_outs',
    'home_woba', 'away_woba',
    'home_platoon_advantage', 'away_platoon_advantage',
    'home_bp_relievers_3d', 'away_bp_relievers_3d',
]


def _probe_valid_cols() -> set:
    r = requests.get(f'{SB}/rest/v1/mlb_game_results?select=*&limit=1', headers=H, timeout=10)
    if r.status_code != 200 or not r.json(): return set()
    return set(r.json()[0].keys())


def _pull_training_data(days: int) -> list:
    d_start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    d_end   = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    valid_cols = _probe_valid_cols()
    features = [c for c in FEATURE_CANDIDATES if c in valid_cols]
    print(f'  {len(features)}/{len(FEATURE_CANDIDATES)} features exist in schema')
    select_str = 'game_id,game_date,home_win,' + ','.join(features)
    rows = []
    for off in range(0, 5000, 500):
        r = requests.get(f'{SB}/rest/v1/mlb_game_results',
            params={'select': select_str,
                    'and': f'(game_date.gte.{d_start},game_date.lte.{d_end})',
                    'home_score': 'not.is.null', 'home_win': 'not.is.null',
                    'limit': 500, 'offset': off, 'order': 'game_date.asc'},
            headers=H, timeout=30)
        chunk = r.json() if isinstance(r.json(), list) else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 500: break
    return rows, features


def _to_float(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def train(days: int = 90, dry_run: bool = False):
    print(f'== MLB ML logreg train · {days}d ==')
    rows, features = _pull_training_data(days)
    print(f'  pulled {len(rows)} resolved games')
    if len(rows) < 200:
        print('  ⚠ insufficient data (<200 games) — aborting'); return

    # Build X, y with NaN-then-imputed missing values
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

    # Impute NaN with column median (matches predict-time behavior).
    # SimpleImputer DROPS all-NaN columns silently — we must drop those
    # from `features` list too, otherwise saved model has mismatched shape.
    # Pre-scan: identify columns with at least one non-NaN.
    valid_col_mask = ~np.all(np.isnan(X), axis=0)
    dropped = [f for f, keep in zip(features, valid_col_mask) if not keep]
    if dropped:
        print(f'  ⚠ dropping {len(dropped)} all-NaN features: {dropped[:6]}...' if len(dropped)>6 else f'  ⚠ dropping all-NaN features: {dropped}')
    features = [f for f, keep in zip(features, valid_col_mask) if keep]
    X = X[:, valid_col_mask]

    imputer = SimpleImputer(strategy='median')
    X_imp = imputer.fit_transform(X)

    # Standardize + fit
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_imp)

    # Train/test split 70/30 for reporting
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
    print(f'  Baseline (always-home holdout): {baseline*100:.1f}%')
    print(f'  Lift over baseline: {(test_acc - baseline)*100:+.1f}pp')

    # Refit on FULL 90d (train + test) for shipping — we've validated on holdout
    lr_full = LogisticRegression(max_iter=1000, C=1.0)
    lr_full.fit(X_scaled, y)

    # Confidence-tier accuracy on test set (using train-only model)
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
        print(f'    {tier}_{side:4s} (p={lo:.2f}-{hi:.2f}): {wins}/{n} = {hit:.1f}%')

    # Save model (coefficients + intercept + feature list + imputer medians + scaler stats)
    model_out = {
        'version': datetime.now(timezone.utc).isoformat(),
        'features': features,
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
    out_path = MODELS_DIR / 'mlb_ml_logreg.json'
    if dry_run:
        print(f'\n  [DRY] would write {out_path}')
    else:
        out_path.write_text(json.dumps(model_out, indent=2))
        print(f'\n  ✓ saved {out_path} ({len(features)} features, test_acc={test_acc*100:.1f}%)')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=90)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    train(days=args.days, dry_run=args.dry_run)
