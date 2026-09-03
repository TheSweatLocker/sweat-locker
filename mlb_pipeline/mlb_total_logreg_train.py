"""MLB TOTAL logistic regression trainer — supervised learning for
over/under picks. Uses ALL resolved games with total_runs + close_total
(much larger sample than 'games where scorer picked a total').

Target: total_runs > close_total (over hit = 1, under hit = 0)

Features scoped to PRE-GAME info only — no leakage from post-game
outcomes. Rolling L10 stats are OK since they're computed before the
current game plays.
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

# Pre-game features from mlb_game_results — same set as ML predictor
# minus features that describe the OUTCOME market (home_ml_odds etc. are
# fine since they're determined pre-game)
FEATURE_CANDIDATES = [
    # Market lines (pre-game info)
    'close_total', 'open_total', 'close_spread', 'open_spread',
    # Starting pitchers
    'home_sp_era', 'away_sp_era', 'home_sp_xera', 'away_sp_xera',
    'home_sp_k_pct', 'away_sp_k_pct', 'home_sp_whiff_rate', 'away_sp_whiff_rate',
    'home_sp_gb_pct', 'away_sp_gb_pct',
    'home_sp_days_rest', 'away_sp_days_rest',
    'home_sp_last5_era', 'away_sp_last5_era',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_pitcher_vs_team_era', 'away_pitcher_vs_team_era',
    'home_pitcher_vs_team_recent_era', 'away_pitcher_vs_team_recent_era',
    'home_pitcher_projected_er', 'away_pitcher_projected_er',
    'home_pitcher_projected_outs', 'away_pitcher_projected_outs',
    # Team offense
    'home_ops', 'away_ops', 'home_wrc_plus', 'away_wrc_plus',
    'home_wrc_vs_opp_hand', 'away_wrc_vs_opp_hand',
    'home_ops_vs_opp_hand', 'away_ops_vs_opp_hand',
    'home_ops_last7', 'away_ops_last7',
    'home_ops_last14', 'away_ops_last14',
    'home_woba', 'away_woba',
    # Team quality (rolling stats — pre-game state)
    'home_runs_per_game', 'away_runs_per_game',
    'home_last5_run_diff', 'away_last5_run_diff',
    'home_last5_runs_per_game', 'away_last5_runs_per_game',
    'home_last10_runs_per_game', 'away_last10_runs_per_game',
    'home_offense_drift', 'away_offense_drift',
    # Bullpen
    'home_bullpen_era', 'away_bullpen_era',
    'home_bp_relievers_3d', 'away_bp_relievers_3d',
    # Park + weather
    'park_run_factor', 'temperature', 'wind_mph',
    # Model predictions (pre-game)
    'projected_total', 'model_pred_total', 'jerry_pred_total',
    'panel_implied_total', 'sp_plus_matchup_total',
]


def _probe_valid_cols():
    r = requests.get(f'{SB}/rest/v1/mlb_game_results?select=*&limit=1', headers=H, timeout=10)
    if r.status_code != 200 or not r.json(): return set()
    return set(r.json()[0].keys())


def _to_float(v):
    if isinstance(v, bool): return 1.0 if v else 0.0
    try: return float(v)
    except (TypeError, ValueError): return None


def train(days: int = 120, dry_run: bool = False):
    print(f'== MLB TOTAL logreg train · {days}d ==')
    valid_cols = _probe_valid_cols()
    features = [c for c in FEATURE_CANDIDATES if c in valid_cols]
    print(f'  {len(features)}/{len(FEATURE_CANDIDATES)} features exist in schema')

    d_start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    d_end   = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    # 2026-09-03: total_runs col is nearly empty (14/1228 populated).
    # Compute from home_score + away_score directly.
    select_str = 'game_id,game_date,home_score,away_score,close_total,' + ','.join(features)
    rows = []
    for off in range(0, 5000, 500):
        r = requests.get(f'{SB}/rest/v1/mlb_game_results',
            params={'select': select_str,
                    'and': f'(game_date.gte.{d_start},game_date.lte.{d_end})',
                    'home_score': 'not.is.null', 'away_score': 'not.is.null',
                    'close_total': 'not.is.null',
                    'limit': 500, 'offset': off, 'order': 'game_date.asc'},
            headers=H, timeout=30)
        chunk = r.json() if isinstance(r.json(), list) else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 500: break
    print(f'  pulled {len(rows)} resolved games w/ score + line')
    if len(rows) < 200:
        print('  ⚠ insufficient data'); return

    X_rows, y_rows = [], []
    for r in rows:
        try:
            hs = float(r.get('home_score'))
            as_ = float(r.get('away_score'))
            line = float(r.get('close_total'))
        except (TypeError, ValueError): continue
        tot = hs + as_
        y = 1 if tot > line else 0  # 1 = OVER hit
        row_vals = [float('nan') if _to_float(r.get(f)) is None else _to_float(r.get(f))
                    for f in features]
        X_rows.append(row_vals)
        y_rows.append(y)
    X = np.array(X_rows); y = np.array(y_rows)
    print(f'  X shape: {X.shape}, over hit rate: {y.mean()*100:.1f}%')

    valid_col_mask = ~np.all(np.isnan(X), axis=0)
    dropped = [f for f, keep in zip(features, valid_col_mask) if not keep]
    if dropped:
        print(f'  ⚠ dropping {len(dropped)} all-NaN features')
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
    print(f'\n  Train {train_acc*100:.1f}%   Test {test_acc*100:.1f}%   Baseline {baseline*100:.1f}%   Lift {(test_acc-baseline)*100:+.1f}pp')

    probs = lr.predict_proba(X_scaled[cut:])[:, 1]  # P(over)
    print(f'\n  Tiered predictions:')
    for lo, hi, tier, side in [(0.65, 1.01, 'PRIME',   'OVER'),
                                (0.55, 0.65, 'STRONG', 'OVER'),
                                (0.45, 0.55, 'COIN',   'NONE'),
                                (0.35, 0.45, 'STRONG', 'UNDER'),
                                (0.00, 0.35, 'PRIME',  'UNDER')]:
        mask = (probs >= lo) & (probs < hi)
        n = int(mask.sum())
        if n == 0: continue
        if side == 'OVER':  wins = int(((y[cut:] == 1) & mask).sum())
        elif side == 'UNDER': wins = int(((y[cut:] == 0) & mask).sum())
        else: wins = 0
        hit = 100*wins/n if n else 0
        print(f'    {tier}_{side:5s}: {wins}/{n} = {hit:.1f}%')

    print(f'\n  Top predictors (by |coef|):')
    for f, c in sorted(zip(features, lr.coef_[0]), key=lambda x: -abs(x[1]))[:12]:
        sign_txt = 'boosts OVER' if c > 0 else 'boosts UNDER'
        print(f'    {f:32s} {c:>+6.3f}  ({sign_txt})')

    lr_full = LogisticRegression(max_iter=1000, C=1.0); lr_full.fit(X_scaled, y)
    out = {
        'version': datetime.now(timezone.utc).isoformat(),
        'features': features,
        'coefficients': lr_full.coef_[0].tolist(),
        'intercept': float(lr_full.intercept_[0]),
        'imputer_medians': imputer.statistics_.tolist(),
        'scaler_mean': scaler.mean_.tolist(),
        'scaler_scale': scaler.scale_.tolist(),
        'meta': {
            'test_accuracy': float(test_acc), 'baseline_accuracy': float(baseline),
            'lift_pp': float((test_acc - baseline) * 100),
            'n_train': int(cut), 'n_test': int(len(y) - cut),
        },
    }
    out_path = MODELS_DIR / 'mlb_total_logreg.json'
    if dry_run: print(f'  [DRY] would write {out_path}')
    else: out_path.write_text(json.dumps(out, indent=2)); print(f'  ✓ saved {out_path}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=120)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    train(days=args.days, dry_run=args.dry_run)
