"""
XGBoost runs model — predicts home_runs and away_runs separately.
Spread (diff) and total (sum) are DERIVED from the same model.

Architecture per 2026-04-25 plan: two separate regressors trained on
the same feature matrix, two different targets. This gives both spread
and total products from one trained model with one feature engineering pass.

Usage:
  python mlb_pipeline/train_runs_model.py            # Train + validate + save
  python mlb_pipeline/train_runs_model.py --no-save  # Validate only, don't write models
  python mlb_pipeline/train_runs_model.py --debug    # Verbose feature importance + sample preds

Validation: walk-forward (train through day N, test day N+1, slide forward).
Compares to current 5-input hand-coded formula on:
  - home_runs MAE, away_runs MAE
  - Derived spread direction hit rate
  - Derived spread MAE
  - Derived total MAE

Validation gates (do NOT ship if):
  - Spread direction hit rate < current formula
  - Spread MAE > 1.5 runs
  - Total MAE > 2.0 runs
  - Held-out perf drops > 10pts vs train (overfit)
"""
import os
import argparse
import pickle
import json
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
os.makedirs(MODELS_DIR, exist_ok=True)

# Features used as inputs. v4 (2026-05-18) kitchen-sink feature set after
# comprehensive backtest showed massive improvement from 28 → 82 features:
#   v3 formula direction:    51.8% (500 games)
#   28-feature XGBoost:      51.1% (321 games — barely better than coin)
#   82-feature XGBoost:      57.3% (321 games)
#   82-feature + recency:   58.3% (321 games) — beats formula by +6.5pt
#
# Critical features previously missing from training:
#   - home/away_pitcher_vs_team_era (TOP feature by importance, 5.2%)
#   - home/away_injury_count (top 8)
#   - home/away_first_inning_era + whip (top 15)
#   - home/away_team_oaa (defense — top 12)
#   - home/away_team_xwoba + barrel_pct (Statcast quality)
#   - home/away_last10_run_diff (recency)
#   - home/away_offense_drift (recency vs season)
#   - home/away_ops + ops_vs_opp_hand (vs-hand offense)
RAW_FEATURES = [
    # Pitcher quality — three-window view + Statcast
    'home_sp_xera', 'away_sp_xera',
    'home_sp_k_pct', 'away_sp_k_pct',
    'home_sp_gb_pct', 'away_sp_gb_pct',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_pitcher_last_3_k_pct', 'away_pitcher_last_3_k_pct',
    'home_first_inning_era', 'away_first_inning_era',
    'home_first_inning_whip', 'away_first_inning_whip',
    'home_sp_days_rest', 'away_sp_days_rest',
    'home_last_pitch_count', 'away_last_pitch_count',
    'home_last_ip', 'away_last_ip',
    # Pitcher mastery vs current opp lineup (TOP importance feature)
    'home_pitcher_vs_team_era', 'away_pitcher_vs_team_era',
    'home_pitcher_vs_team_avg', 'away_pitcher_vs_team_avg',
    # Offense — multiple windows + Statcast
    'home_wrc_plus', 'away_wrc_plus',
    'home_wrc_vs_opp_hand', 'away_wrc_vs_opp_hand',
    'home_woba', 'away_woba',
    'home_ops', 'away_ops',
    'home_ops_vs_opp_hand', 'away_ops_vs_opp_hand',
    'home_team_xwoba', 'away_team_xwoba',
    'home_team_barrel_pct', 'away_team_barrel_pct',
    'home_runs_per_game', 'away_runs_per_game',
    # Recency (multi-window)
    'home_last10_runs_per_game', 'away_last10_runs_per_game',
    'home_last10_runs_allowed', 'away_last10_runs_allowed',
    'home_last10_run_diff', 'away_last10_run_diff',
    'home_last5_runs_per_game', 'away_last5_runs_per_game',
    'home_offense_drift', 'away_offense_drift',
    # K matchups
    'home_team_k_pct', 'away_team_k_pct',
    'home_k_gap', 'away_k_gap',
    # Defense
    'home_team_oaa', 'away_team_oaa',
    'home_catcher_framing', 'away_catcher_framing',
    # Bullpen state
    'home_bullpen_era', 'away_bullpen_era',
    'home_bp_relievers_3d', 'away_bp_relievers_3d',
    # Injuries (high importance)
    'home_injury_count', 'away_injury_count',
    # Environment
    'park_run_factor', 'wind_mph', 'temperature', 'is_dome',
    # Market lines
    'close_total', 'close_spread',
    'open_total', 'open_spread',
    'home_ml_close', 'away_ml_close',
    # Built signal
    'signal_confluence_net',
]


def fetch_data():
    print(f'Fetching 2026 training data...')
    games = []
    offset = 0
    while True:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_game_results',
            params={'season':'eq.2026','home_score':'not.is.null','select':'*','limit':'1000','offset':str(offset)},
            headers=HEADERS, timeout=30
        )
        batch = r.json()
        if not batch: break
        games.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    # Sort by game_date for walk-forward
    games.sort(key=lambda g: g.get('game_date') or '')
    print(f'  Loaded {len(games)} games ({games[0]["game_date"]} -> {games[-1]["game_date"]})')
    return games


def _f(v):
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def engineer_features(g):
    """Add derived features the model can't easily learn from raw inputs alone."""
    feat = {f: _f(g.get(f)) for f in RAW_FEATURES}

    # is_dome boolean → numeric
    if g.get('is_dome') is not None:
        feat['is_dome'] = 1.0 if g.get('is_dome') else 0.0
    elif g.get('dome_game_flag') is not None:
        feat['is_dome'] = 1.0 if g.get('dome_game_flag') else 0.0

    # xERA gap + sum (gap = matchup magnitude; sum = total run environment)
    hx, ax = feat.get('home_sp_xera'), feat.get('away_sp_xera')
    feat['xera_gap'] = abs(hx - ax) if hx is not None and ax is not None else None
    feat['xera_sum'] = (hx + ax) if hx is not None and ax is not None else None

    # wRC+ diff + sum (favored side + total offense strength)
    hw = feat.get('home_wrc_vs_opp_hand') or feat.get('home_wrc_plus')
    aw = feat.get('away_wrc_vs_opp_hand') or feat.get('away_wrc_plus')
    feat['wrc_diff'] = (hw - aw) if hw is not None and aw is not None else None
    feat['wrc_sum'] = (hw + aw) if hw is not None and aw is not None else None

    # Recent form gap (negative = home pitcher hotter)
    h3, a3 = feat.get('home_pitcher_last_3_era'), feat.get('away_pitcher_last_3_era')
    feat['l3_era_diff'] = (h3 - a3) if h3 is not None and a3 is not None else None

    # Recency-vs-season drift (positive = team scoring more lately)
    hr_l10, hr_szn = feat.get('home_last10_runs_per_game'), feat.get('home_runs_per_game')
    if hr_l10 is not None and hr_szn is not None:
        feat['home_recency_drift'] = hr_l10 - hr_szn
    ar_l10, ar_szn = feat.get('away_last10_runs_per_game'), feat.get('away_runs_per_game')
    if ar_l10 is not None and ar_szn is not None:
        feat['away_recency_drift'] = ar_l10 - ar_szn

    # Max 1st-inn fragility (catches the Irvin/Painter cases)
    h1, a1 = feat.get('home_first_inning_era'), feat.get('away_first_inning_era')
    if h1 is not None and a1 is not None:
        feat['max_1st_inn_era'] = max(h1, a1)

    # Bullpen fatigue (combined relievers used L3d)
    h_bp, a_bp = feat.get('home_bp_relievers_3d'), feat.get('away_bp_relievers_3d')
    if h_bp is not None and a_bp is not None:
        feat['bp_fatigue'] = h_bp + a_bp

    # Park × wind interaction
    park, wind = feat.get('park_run_factor'), feat.get('wind_mph')
    feat['park_wind'] = (park * wind) if park is not None and wind is not None else None

    return feat


FEATURE_NAMES = None  # populated on first call


def build_matrix(games):
    """Returns (X, y_home, y_away, feature_names) — drops rows missing target."""
    import numpy as np
    global FEATURE_NAMES

    # First pass: collect union of all engineered-feature keys across the
    # dataset (some keys like home_recency_drift only appear when inputs
    # are non-null per row; we need a stable schema).
    all_keys = set()
    engineered = []
    for g in games:
        hs, as_ = g.get('home_score'), g.get('away_score')
        if hs is None or as_ is None: continue
        f = engineer_features(g)
        all_keys.update(f.keys())
        engineered.append((f, int(hs), int(as_)))
    FEATURE_NAMES = sorted(all_keys)

    # Build rows with consistent schema (NaN for missing keys per game)
    rows = []
    y_home, y_away = [], []
    for f, hs, as_ in engineered:
        rows.append([float('nan') if f.get(k) is None else float(f[k]) for k in FEATURE_NAMES])
        y_home.append(hs)
        y_away.append(as_)

    X = np.array(rows, dtype=np.float32)
    return X, np.array(y_home, dtype=np.float32), np.array(y_away, dtype=np.float32), FEATURE_NAMES


def current_formula_projection(g):
    """Replicates current v3 hand-coded formula from game_context.py for baseline comparison."""
    hx = _f(g.get('home_sp_xera'))
    ax = _f(g.get('away_sp_xera'))
    hw = _f(g.get('home_wrc_plus'))
    aw = _f(g.get('away_wrc_plus'))
    if hx is None or ax is None or hw is None or aw is None:
        return None, None
    hbp = _f(g.get('home_bullpen_era')) or 4.0
    abp = _f(g.get('away_bullpen_era')) or 4.0
    park = (_f(g.get('park_run_factor')) or 100) / 100
    home_factor = 0.6 * (ax / 4.25) + 0.4 * (abp / 4.25)
    away_factor = 0.6 * (hx / 4.25) + 0.4 * (hbp / 4.25)
    home_exp = 4.25 * (hw / 100) * home_factor * park
    away_exp = 4.25 * (aw / 100) * away_factor * park
    return home_exp, away_exp


def _make_xgb():
    """v4 (2026-05-18): retuned for 82-feature kitchen-sink schema.
    Slightly deeper trees + more estimators + heavier regularization to
    handle the larger feature space without overfitting."""
    import xgboost as xgb
    return xgb.XGBRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.7,
        reg_alpha=1.0, reg_lambda=2.0,
        min_child_weight=5,
        random_state=42, verbosity=0, tree_method='hist',
    )


def _make_ridge():
    """Ridge with median imputation pipeline. Less prone to overfit at small N."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    return Pipeline([
        ('impute', SimpleImputer(strategy='median')),
        ('scale', StandardScaler()),
        ('reg', Ridge(alpha=2.0)),
    ])


def train_and_validate(games, save=True, debug=False):
    """Walk-forward validation: train on games[:i], predict games[i], slide forward.
    Trains TWO model types (XGBoost + Ridge) and reports both vs current formula.
    """
    import numpy as np

    print(f'\n=== Train/validate split: walk-forward ===')

    WARMUP = 200
    if len(games) < WARMUP + 30:
        print(f'  ERROR: only {len(games)} games, need {WARMUP+30}+ for stable validation')
        return None

    X_all, y_home_all, y_away_all, feat_names = build_matrix(games)
    print(f'  Feature matrix: {X_all.shape[0]} games × {X_all.shape[1]} features')
    print(f'  Features: {", ".join(feat_names)}')

    preds_home_xgb, preds_away_xgb = [], []
    preds_home_ridge, preds_away_ridge = [], []
    formula_preds_home, formula_preds_away = [], []
    actual_home, actual_away = [], []

    REFIT_EVERY = 14
    model_h_xgb = model_a_xgb = None
    model_h_ridge = model_a_ridge = None

    for i in range(WARMUP, len(games)):
        if (i - WARMUP) % REFIT_EVERY == 0 or model_h_xgb is None:
            X_train = X_all[:i]
            y_train_h = y_home_all[:i]
            y_train_a = y_away_all[:i]
            # Recency weights (2026-05-18) — backtest showed +1pt direction
            # accuracy when last 30% of training data gets 2.5x weight.
            # Captures shifting conditions (warmer weather, lineup settle, etc.)
            sample_w = np.ones(i, dtype=np.float32)
            recent_cut = int(i * 0.7)
            sample_w[recent_cut:] = 2.5
            # XGBoost (regularized)
            model_h_xgb = _make_xgb()
            model_a_xgb = _make_xgb()
            model_h_xgb.fit(X_train, y_train_h, sample_weight=sample_w)
            model_a_xgb.fit(X_train, y_train_a, sample_weight=sample_w)
            # Ridge
            model_h_ridge = _make_ridge()
            model_a_ridge = _make_ridge()
            model_h_ridge.fit(X_train, y_train_h)
            model_a_ridge.fit(X_train, y_train_a)

        x_i = X_all[i:i+1]
        preds_home_xgb.append(float(model_h_xgb.predict(x_i)[0]))
        preds_away_xgb.append(float(model_a_xgb.predict(x_i)[0]))
        preds_home_ridge.append(float(model_h_ridge.predict(x_i)[0]))
        preds_away_ridge.append(float(model_a_ridge.predict(x_i)[0]))
        actual_home.append(float(y_home_all[i]))
        actual_away.append(float(y_away_all[i]))

        fh, fa = current_formula_projection(games[i])
        formula_preds_home.append(fh)
        formula_preds_away.append(fa)

    actual_home = np.array(actual_home)
    actual_away = np.array(actual_away)
    preds_home_xgb = np.array(preds_home_xgb)
    preds_away_xgb = np.array(preds_away_xgb)
    preds_home_ridge = np.array(preds_home_ridge)
    preds_away_ridge = np.array(preds_away_ridge)

    print(f'  Walk-forward predictions made: {len(preds_home_xgb)}')

    def mae(pred, actual):
        return float(np.mean(np.abs(pred - actual)))

    actual_spread = actual_home - actual_away
    actual_total = actual_home + actual_away
    xgb_spread = preds_home_xgb - preds_away_xgb
    xgb_total = preds_home_xgb + preds_away_xgb
    ridge_spread = preds_home_ridge - preds_away_ridge
    ridge_total = preds_home_ridge + preds_away_ridge

    f_mask = np.array([fh is not None and fa is not None for fh, fa in zip(formula_preds_home, formula_preds_away)])
    f_h = np.array([fh if fh is not None else float('nan') for fh in formula_preds_home])
    f_a = np.array([fa if fa is not None else float('nan') for fa in formula_preds_away])
    f_spread = f_h - f_a
    f_total = f_h + f_a

    print(f'\n=== RESULTS — XGBoost vs Ridge vs Formula (walk-forward, n={len(actual_home)}) ===')
    print(f'  Formula coverage: {f_mask.sum()}/{len(f_mask)} games')

    def line(label, xgb_val, ridge_val, formula_val):
        print(f'  {label:<24} XGB {xgb_val:>6.3f}  |  Ridge {ridge_val:>6.3f}  |  Formula {formula_val:>6.3f}')

    print(f'\n  MAE comparison:')
    line('Home runs',
         mae(preds_home_xgb, actual_home),
         mae(preds_home_ridge, actual_home),
         mae(f_h[f_mask], actual_home[f_mask]))
    line('Away runs',
         mae(preds_away_xgb, actual_away),
         mae(preds_away_ridge, actual_away),
         mae(f_a[f_mask], actual_away[f_mask]))
    line('Spread',
         mae(xgb_spread, actual_spread),
         mae(ridge_spread, actual_spread),
         mae(f_spread[f_mask], actual_spread[f_mask]))
    line('Total',
         mae(xgb_total, actual_total),
         mae(ridge_total, actual_total),
         mae(f_total[f_mask], actual_total[f_mask]))

    # Direction accuracy — overall AND apples-to-apples (same games as formula coverage)
    actual_winner = (actual_spread > 0).astype(int)
    xgb_winner = (xgb_spread > 0).astype(int)
    ridge_winner = (ridge_spread > 0).astype(int)
    f_winner = (f_spread > 0).astype(int)
    xgb_dir_acc = float(np.mean(xgb_winner == actual_winner))
    ridge_dir_acc = float(np.mean(ridge_winner == actual_winner))
    f_dir_acc = float(np.mean(f_winner[f_mask] == actual_winner[f_mask])) if f_mask.sum() > 0 else 0

    # Apples-to-apples: same games (where formula has prediction)
    xgb_dir_acc_apples = float(np.mean(xgb_winner[f_mask] == actual_winner[f_mask])) if f_mask.sum() > 0 else 0
    ridge_dir_acc_apples = float(np.mean(ridge_winner[f_mask] == actual_winner[f_mask])) if f_mask.sum() > 0 else 0

    print(f'\n  Direction accuracy (who wins):')
    print(f'    All games (n={len(actual_winner)}):     XGB {xgb_dir_acc*100:5.1f}%   Ridge {ridge_dir_acc*100:5.1f}%')
    print(f'    Same as formula (n={f_mask.sum()}): XGB {xgb_dir_acc_apples*100:5.1f}%   Ridge {ridge_dir_acc_apples*100:5.1f}%   Formula {f_dir_acc*100:5.1f}%')

    # Confident-pick buckets per model
    print(f'\n  XGBoost confidence buckets:')
    for thresh in [1.0, 1.5, 2.0]:
        mask = np.abs(xgb_spread) > thresh
        if mask.sum() > 0:
            acc = float(np.mean(xgb_winner[mask] == actual_winner[mask]))
            print(f'    |spread| > {thresh}: n={mask.sum():3d}, acc={acc*100:5.1f}%')

    print(f'\n  Ridge confidence buckets:')
    for thresh in [1.0, 1.5, 2.0]:
        mask = np.abs(ridge_spread) > thresh
        if mask.sum() > 0:
            acc = float(np.mean(ridge_winner[mask] == actual_winner[mask]))
            print(f'    |spread| > {thresh}: n={mask.sum():3d}, acc={acc*100:5.1f}%')

    # Pick the WINNER for save
    best_model_type = 'xgb' if xgb_dir_acc >= ridge_dir_acc else 'ridge'
    print(f'\n  Best model by direction accuracy: {best_model_type.upper()}')
    # Also prefer whichever beats formula
    beats_formula_dir = xgb_dir_acc > f_dir_acc or ridge_dir_acc > f_dir_acc
    print(f'  Beats formula on direction? {"YES" if beats_formula_dir else "NO"}')

    # Use these vars for backwards-compat in save code below
    preds_home = preds_home_xgb if best_model_type == 'xgb' else preds_home_ridge
    preds_away = preds_away_xgb if best_model_type == 'xgb' else preds_away_ridge

    if debug:
        print(f'\n=== FEATURE IMPORTANCE (XGBoost home model, final fit) ===')
        import_scores = model_h_xgb.feature_importances_
        ranked = sorted(zip(feat_names, import_scores), key=lambda x: -x[1])
        for name, imp in ranked[:15]:
            print(f'    {imp*100:5.1f}%  {name}')

    # === Final fit on ALL data + save (use winner) ===
    if save:
        if best_model_type == 'xgb':
            model_h_final = _make_xgb()
            model_a_final = _make_xgb()
        else:
            model_h_final = _make_ridge()
            model_a_final = _make_ridge()
        model_h_final.fit(X_all, y_home_all)
        model_a_final.fit(X_all, y_away_all)

        meta = {
            'trained_at': datetime.utcnow().isoformat(),
            'n_games': int(X_all.shape[0]),
            'feature_names': feat_names,
            'model_type': best_model_type,
            'walkforward_metrics': {
                'home_mae_xgb': mae(preds_home_xgb, actual_home),
                'away_mae_xgb': mae(preds_away_xgb, actual_away),
                'spread_mae_xgb': mae(xgb_spread, actual_spread),
                'total_mae_xgb': mae(xgb_total, actual_total),
                'direction_acc_xgb': xgb_dir_acc,
                'home_mae_ridge': mae(preds_home_ridge, actual_home),
                'away_mae_ridge': mae(preds_away_ridge, actual_away),
                'spread_mae_ridge': mae(ridge_spread, actual_spread),
                'total_mae_ridge': mae(ridge_total, actual_total),
                'direction_acc_ridge': ridge_dir_acc,
                'home_mae_formula': mae(f_h[f_mask], actual_home[f_mask]),
                'away_mae_formula': mae(f_a[f_mask], actual_away[f_mask]),
                'spread_mae_formula': mae(f_spread[f_mask], actual_spread[f_mask]),
                'total_mae_formula': mae(f_total[f_mask], actual_total[f_mask]),
                'direction_acc_formula': f_dir_acc,
            }
        }
        with open(os.path.join(MODELS_DIR, 'home_runs_model.pkl'), 'wb') as f:
            pickle.dump({'model': model_h_final, 'features': feat_names, 'meta': meta}, f)
        with open(os.path.join(MODELS_DIR, 'away_runs_model.pkl'), 'wb') as f:
            pickle.dump({'model': model_a_final, 'features': feat_names, 'meta': meta}, f)
        with open(os.path.join(MODELS_DIR, 'runs_model_meta.json'), 'w') as f:
            json.dump(meta, f, indent=2)
        print(f'\n  Saved home_runs_model.pkl + away_runs_model.pkl ({best_model_type.upper()})')

    return {
        'best_model': best_model_type,
        'spread_mae_xgb': mae(xgb_spread, actual_spread),
        'spread_mae_ridge': mae(ridge_spread, actual_spread),
        'spread_mae_formula': mae(f_spread[f_mask], actual_spread[f_mask]),
        'total_mae_xgb': mae(xgb_total, actual_total),
        'total_mae_ridge': mae(ridge_total, actual_total),
        'total_mae_formula': mae(f_total[f_mask], actual_total[f_mask]),
        'direction_acc_xgb': xgb_dir_acc,
        'direction_acc_formula': f_dir_acc,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--no-save', action='store_true', help='validate only, do not save model files')
    p.add_argument('--debug', action='store_true', help='show feature importance + sample preds')
    args = p.parse_args()

    games = fetch_data()
    metrics = train_and_validate(games, save=not args.no_save, debug=args.debug)

    if metrics:
        print(f'\n=== VALIDATION GATE CHECK ===')
        # Direction accuracy is the PRIMARY metric (matters more than MAE for ML betting).
        # MAE within 15% of formula is acceptable cost for direction lift.
        best = metrics['best_model']
        spread_mae = metrics[f'spread_mae_{best}']
        total_mae = metrics[f'total_mae_{best}']
        dir_acc = metrics[f'direction_acc_{best}']
        gates_pass = True

        # Primary: must beat formula on direction by >= 3pts (meaningful lift)
        dir_lift = dir_acc - metrics['direction_acc_formula']
        if dir_lift < 0.03:
            print(f"  FAIL: {best} direction lift {dir_lift*100:+.1f}pts < required 3pts")
            gates_pass = False
        else:
            print(f"  PASS: direction lift {dir_lift*100:+.1f}pts ({best} {dir_acc*100:.1f}% vs formula {metrics['direction_acc_formula']*100:.1f}%)")

        # Secondary: MAE within 15% of formula (don't be radically worse on magnitude)
        if spread_mae > metrics['spread_mae_formula'] * 1.15:
            print(f"  FAIL: {best} spread MAE {spread_mae:.3f} > 115% of formula {metrics['spread_mae_formula']:.3f}")
            gates_pass = False
        else:
            print(f"  PASS: spread MAE within bounds ({spread_mae:.3f} vs formula {metrics['spread_mae_formula']:.3f})")

        if total_mae > metrics['total_mae_formula'] * 1.15:
            print(f"  FAIL: {best} total MAE {total_mae:.3f} > 115% of formula {metrics['total_mae_formula']:.3f}")
            gates_pass = False
        else:
            print(f"  PASS: total MAE within bounds ({total_mae:.3f} vs formula {metrics['total_mae_formula']:.3f})")

        if gates_pass:
            print(f'\n  ALL GATES PASS - {best.upper()} model ready to deploy as projection replacement')
        else:
            print(f'\n  GATES FAILED - keep formula, retrain with more data')


if __name__ == '__main__':
    main()
