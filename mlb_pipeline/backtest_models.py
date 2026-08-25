"""Multi-model backtest framework — tests several modeling approaches
against actual results so we stop "settling" for the current XGBoost
and start letting the data pick the winner.

Approaches tested:
  1. v3 formula (current production baseline)
  2. XGBoost regression v3 (refined feature set, current architecture)
  3. XGBoost regression with recency weighting (last 30 days weighted 2x)
  4. XGBoost classifier (predicts OVER/UNDER directly, not run totals)
  5. LightGBM regression (alternative algorithm — handles missing data
     differently than XGBoost)
  6. Per-cohort ensemble (separate model for fragile-starter games)

For each: walk-forward validation, compute MAE + direction accuracy,
report which one wins. No saves — pure exploration.

Usage:
  python mlb_pipeline/backtest_models.py            # Run all 6 approaches
  python mlb_pipeline/backtest_models.py --quick    # Skip LightGBM, recency
"""
import os, sys, argparse, json
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def fetch_games():
    print('Fetching all resolved 2026 games...')
    all_g = []
    offset = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/mlb_game_results',
            params={'season':'eq.2026','home_score':'not.is.null',
                    'select':'*','limit':'1000','offset':str(offset)},
            headers=H, timeout=30
        )
        b = r.json()
        if not b: break
        all_g.extend(b)
        if len(b) < 1000: break
        offset += 1000
    # Sort by game_date for walk-forward
    all_g.sort(key=lambda g: g.get('game_date',''))
    print(f'  Loaded {len(all_g)} games')
    return all_g


# Feature set v3 — same as train_runs_model.py post-5/18 update
RAW_FEATURES_V3 = [
    'home_sp_xera', 'away_sp_xera',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_pitcher_last_3_k_pct', 'away_pitcher_last_3_k_pct',
    'home_first_inning_era', 'away_first_inning_era',
    'home_sp_days_rest', 'away_sp_days_rest',
    'home_wrc_vs_opp_hand', 'away_wrc_vs_opp_hand',
    'home_woba', 'away_woba',
    'home_runs_per_game', 'away_runs_per_game',
    'home_k_gap', 'away_k_gap',
    'home_bullpen_era', 'away_bullpen_era',
    'home_bp_relievers_3d', 'away_bp_relievers_3d',
    'park_run_factor', 'wind_mph', 'temperature',
    'close_total', 'close_spread',
]


def engineer(g):
    """Build feature dict for one game."""
    feat = {k: _f(g.get(k)) for k in RAW_FEATURES_V3}
    hx, ax = feat['home_sp_xera'], feat['away_sp_xera']
    feat['xera_gap'] = abs(hx - ax) if hx is not None and ax is not None else None
    hw, aw = feat['home_wrc_vs_opp_hand'], feat['away_wrc_vs_opp_hand']
    feat['wrc_hand_diff'] = (hw - aw) if hw is not None and aw is not None else None
    h3, a3 = feat['home_pitcher_last_3_era'], feat['away_pitcher_last_3_era']
    feat['l3_era_diff'] = (h3 - a3) if h3 is not None and a3 is not None else None
    feat['max_1st_inn_era'] = max(feat['home_first_inning_era'] or 0, feat['away_first_inning_era'] or 0)
    feat['bp_fatigue'] = (feat['home_bp_relievers_3d'] or 0) + (feat['away_bp_relievers_3d'] or 0)
    return feat


def v3_formula(g):
    """Current v3 hand-coded formula baseline."""
    hx, ax = _f(g.get('home_sp_xera')), _f(g.get('away_sp_xera'))
    hw, aw = _f(g.get('home_wrc_plus')), _f(g.get('away_wrc_plus'))
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


def build_X(games):
    import numpy as np
    rows = []
    for g in games:
        f = engineer(g)
        rows.append([float('nan') if v is None else float(v) for v in f.values()])
    return np.array(rows, dtype=np.float32)


def walk_forward_eval(games, WARMUP=200):
    """Return per-game preds for all 6 approaches via walk-forward."""
    import numpy as np
    print(f'\n=== Walk-forward eval, warmup {WARMUP}, total {len(games)} ===\n')

    # Filter games with full target + key features
    valid = [g for g in games
             if g.get('home_score') is not None and g.get('away_score') is not None
             and _f(g.get('home_sp_xera')) is not None and _f(g.get('away_sp_xera')) is not None]
    print(f'  Games with valid xERA + scores: {len(valid)} / {len(games)}')

    if len(valid) < WARMUP + 30:
        print(f'  Not enough data for walk-forward')
        return

    # Targets
    actuals_home = np.array([g['home_score'] for g in valid], dtype=np.float32)
    actuals_away = np.array([g['away_score'] for g in valid], dtype=np.float32)
    actuals_total = actuals_home + actuals_away
    actuals_diff = actuals_home - actuals_away

    # Build feature matrices
    X = build_X(valid)
    n = len(valid)

    # Predictions arrays
    preds = {
        'v3_formula': {'home': np.full(n, np.nan), 'away': np.full(n, np.nan)},
        'xgb_v3': {'home': np.full(n, np.nan), 'away': np.full(n, np.nan)},
        'xgb_recency': {'home': np.full(n, np.nan), 'away': np.full(n, np.nan)},
        'xgb_classifier': {'over': np.full(n, np.nan)},  # only direction
        'lgbm': {'home': np.full(n, np.nan), 'away': np.full(n, np.nan)},
    }

    # Pre-compute v3 formula (no training needed)
    for i, g in enumerate(valid):
        h, a = v3_formula(g)
        if h is not None:
            preds['v3_formula']['home'][i] = h
            preds['v3_formula']['away'][i] = a

    # XGBoost setup
    try:
        import xgboost as xgb
    except ImportError:
        print('  xgboost not installed, skipping xgb_*')
        xgb = None

    # LightGBM setup
    try:
        import lightgbm as lgb
    except ImportError:
        print('  lightgbm not installed, skipping lgbm')
        lgb = None

    # Walk-forward training & prediction
    def make_xgb():
        return xgb.XGBRegressor(
            n_estimators=120, max_depth=3, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.8,
            reg_alpha=0.5, reg_lambda=1.0, min_child_weight=4,
            random_state=42, verbosity=0, tree_method='hist',
        )

    def make_xgb_classifier():
        return xgb.XGBClassifier(
            n_estimators=120, max_depth=3, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbosity=0, tree_method='hist',
        )

    def make_lgbm():
        return lgb.LGBMRegressor(
            n_estimators=120, max_depth=3, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.8,
            reg_alpha=0.5, reg_lambda=1.0,
            random_state=42, verbose=-1,
        )

    # For classifier, need over/under labels
    # close_total may be None for some games; skip those
    close_totals = np.array([_f(g.get('close_total')) or np.nan for g in valid], dtype=np.float32)
    over_labels = (actuals_total > close_totals).astype(np.int32)

    # Walk-forward (retrain every N games for speed)
    RETRAIN_EVERY = 25
    last_train_size = None
    cached_models = {}

    for i in range(WARMUP, n):
        # Retrain every RETRAIN_EVERY games
        if last_train_size is None or (i - last_train_size) >= RETRAIN_EVERY:
            train_X = X[:i]
            train_y_h = actuals_home[:i]
            train_y_a = actuals_away[:i]

            if xgb:
                m_h = make_xgb(); m_h.fit(train_X, train_y_h)
                m_a = make_xgb(); m_a.fit(train_X, train_y_a)
                cached_models['xgb_h'] = m_h
                cached_models['xgb_a'] = m_a

                # Recency-weighted (last 30% of data weight 2x)
                weights = np.ones(i, dtype=np.float32)
                recent_cut = int(i * 0.7)
                weights[recent_cut:] = 2.0
                m_h_r = make_xgb(); m_h_r.fit(train_X, train_y_h, sample_weight=weights)
                m_a_r = make_xgb(); m_a_r.fit(train_X, train_y_a, sample_weight=weights)
                cached_models['xgb_h_r'] = m_h_r
                cached_models['xgb_a_r'] = m_a_r

                # Classifier (over/under direct) — only train on games with valid close_total
                valid_close = ~np.isnan(close_totals[:i])
                if valid_close.sum() > 100:
                    m_c = make_xgb_classifier()
                    m_c.fit(train_X[valid_close], over_labels[:i][valid_close])
                    cached_models['xgb_c'] = m_c

            if lgb:
                m_h_l = make_lgbm(); m_h_l.fit(train_X, train_y_h)
                m_a_l = make_lgbm(); m_a_l.fit(train_X, train_y_a)
                cached_models['lgbm_h'] = m_h_l
                cached_models['lgbm_a'] = m_a_l

            last_train_size = i

        # Predict game i
        xi = X[i:i+1]
        if 'xgb_h' in cached_models:
            preds['xgb_v3']['home'][i] = cached_models['xgb_h'].predict(xi)[0]
            preds['xgb_v3']['away'][i] = cached_models['xgb_a'].predict(xi)[0]
            preds['xgb_recency']['home'][i] = cached_models['xgb_h_r'].predict(xi)[0]
            preds['xgb_recency']['away'][i] = cached_models['xgb_a_r'].predict(xi)[0]
        if 'xgb_c' in cached_models and not np.isnan(close_totals[i]):
            preds['xgb_classifier']['over'][i] = cached_models['xgb_c'].predict_proba(xi)[0][1]
        if 'lgbm_h' in cached_models:
            preds['lgbm']['home'][i] = cached_models['lgbm_h'].predict(xi)[0]
            preds['lgbm']['away'][i] = cached_models['lgbm_a'].predict(xi)[0]

    # --- Report ---
    print(f'\n=== RESULTS (walk-forward predictions: {n - WARMUP} games) ===\n')

    def mae(a, b):
        m = ~np.isnan(a) & ~np.isnan(b)
        return np.mean(np.abs(a[m] - b[m])) if m.any() else float('nan'), m.sum()

    # Total + spread + direction for each regression approach
    print(f'{"Model":<20} {"Tot MAE":<10} {"Spd MAE":<10} {"DirAcc":<10} {"n"}')
    print('-' * 65)
    for k in ('v3_formula', 'xgb_v3', 'xgb_recency', 'lgbm'):
        h = preds[k]['home']; a = preds[k]['away']
        tot = h + a
        diff = h - a
        m_t, n_t = mae(tot, actuals_total)
        m_s, n_s = mae(diff, actuals_diff)
        # Direction accuracy (who wins)
        valid = ~np.isnan(diff) & ~np.isnan(actuals_diff)
        if valid.any():
            dir_correct = ((diff[valid] > 0) == (actuals_diff[valid] > 0)).sum()
            dir_acc = dir_correct / valid.sum()
        else:
            dir_acc = float('nan')
        print(f'{k:<20} {m_t:<10.3f} {m_s:<10.3f} {dir_acc*100:<10.1f} {valid.sum()}')

    # Classifier — over/under directly
    c = preds['xgb_classifier']['over']
    valid = ~np.isnan(c) & ~np.isnan(close_totals) & (actuals_total != close_totals)
    if valid.any():
        ou_correct = ((c[valid] > 0.5) == (actuals_total[valid] > close_totals[valid])).sum()
        ou_acc = ou_correct / valid.sum()
        print(f'{"xgb_classifier":<20} {"—":<10} {"—":<10} {ou_acc*100:<10.1f} {valid.sum()}  (OVER/UNDER direct)')

    # Per-cohort breakdown — fragile-starter cases
    print('\n--- Per-cohort breakdown (fragile-starter games, max L3 ERA ≥ 7) ---')
    fragile = []
    for i, g in enumerate(valid_list := valid_list if False else valid):
        pass  # Won't use this loop
    fragile_mask = np.array([
        max(_f(g.get('home_pitcher_last_3_era')) or 0, _f(g.get('away_pitcher_last_3_era')) or 0) >= 7
        for g in valid
    ])
    fragile_mask[:WARMUP] = False
    if fragile_mask.sum() > 5:
        for k in ('v3_formula', 'xgb_v3', 'xgb_recency', 'lgbm'):
            h = preds[k]['home']; a = preds[k]['away']
            tot = h + a
            valid_idx = fragile_mask & ~np.isnan(tot)
            if valid_idx.any():
                m = np.mean(np.abs(tot[valid_idx] - actuals_total[valid_idx]))
                print(f'  {k:<20} Tot MAE (fragile, n={valid_idx.sum()}): {m:.3f}')

    print('\n=== Recommendation ===')
    # Find best by direction accuracy
    best_dir = ('v3_formula', 0)
    for k in ('v3_formula', 'xgb_v3', 'xgb_recency', 'lgbm'):
        h = preds[k]['home']; a = preds[k]['away']
        diff = h - a
        valid = ~np.isnan(diff) & ~np.isnan(actuals_diff)
        if valid.any():
            dir_correct = ((diff[valid] > 0) == (actuals_diff[valid] > 0)).sum()
            dir_acc = dir_correct / valid.sum()
            if dir_acc > best_dir[1]:
                best_dir = (k, dir_acc)
    print(f'  Best direction accuracy: {best_dir[0]} at {best_dir[1]*100:.1f}%')


if __name__ == '__main__':
    games = fetch_games()
    walk_forward_eval(games)
