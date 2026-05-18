"""Comprehensive backtest — actually use everything we have.

Tests:
  1. v3 formula (baseline)
  2. XGBoost regression (current 28 features)
  3. XGBoost regression (60+ features — kitchen sink)
  4. XGBoost regression (60+ features + recency weighted)
  5. XGBoost ML classifier (predicts home_win directly — binary)
  6. XGBoost total classifier (predicts OVER directly — binary)
  7. Per-cohort subset analysis (where IS the model predictive?)

Reports MAE + direction accuracy. Highlights subsets where any model
exceeds 58% direction (the threshold for "real edge after juice").
"""
import os, sys
import numpy as np
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
    all_g.sort(key=lambda g: g.get('game_date',''))
    print(f'  Loaded {len(all_g)} games')
    return all_g


# Feature sets to test
FEATURES_MINIMAL = [
    'home_sp_xera', 'away_sp_xera',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_wrc_vs_opp_hand', 'away_wrc_vs_opp_hand',
    'home_runs_per_game', 'away_runs_per_game',
    'home_k_gap', 'away_k_gap',
    'home_lineup_weight', 'away_lineup_weight',
    'park_run_factor', 'wind_mph',
    'close_total', 'close_spread',
    'nrfi_score',
]

# Kitchen sink — use everything plausibly predictive
FEATURES_KITCHEN_SINK = [
    # Pitcher quality (multiple windows)
    'home_sp_xera', 'away_sp_xera',
    'home_sp_k_pct', 'away_sp_k_pct',
    'home_sp_gb_pct', 'away_sp_gb_pct',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_pitcher_last_3_k_pct', 'away_pitcher_last_3_k_pct',
    'home_sp_days_rest', 'away_sp_days_rest',
    'home_last_pitch_count', 'away_last_pitch_count',
    'home_last_ip', 'away_last_ip',
    # 1st-inning fragility family
    'home_first_inning_era', 'away_first_inning_era',
    'home_first_inning_whip', 'away_first_inning_whip',
    # Pitcher mastery vs opp lineup
    'home_pitcher_vs_team_era', 'away_pitcher_vs_team_era',
    'home_pitcher_vs_team_avg', 'away_pitcher_vs_team_avg',
    # Offense (multiple windows)
    'home_wrc_plus', 'away_wrc_plus',
    'home_wrc_vs_opp_hand', 'away_wrc_vs_opp_hand',
    'home_woba', 'away_woba',
    'home_ops', 'away_ops',
    'home_ops_vs_opp_hand', 'away_ops_vs_opp_hand',
    'home_team_xwoba', 'away_team_xwoba',
    'home_team_barrel_pct', 'away_team_barrel_pct',
    'home_runs_per_game', 'away_runs_per_game',
    # Recency
    'home_last10_runs_per_game', 'away_last10_runs_per_game',
    'home_last10_runs_allowed', 'away_last10_runs_allowed',
    'home_last10_run_diff', 'away_last10_run_diff',
    'home_last5_runs_per_game', 'away_last5_runs_per_game',
    'home_offense_drift', 'away_offense_drift',
    # K matchup
    'home_team_k_pct', 'away_team_k_pct',
    'home_k_gap', 'away_k_gap',
    # Defense
    'home_team_oaa', 'away_team_oaa',
    'home_catcher_framing', 'away_catcher_framing',
    # Bullpen
    'home_bullpen_era', 'away_bullpen_era',
    'home_bp_relievers_3d', 'away_bp_relievers_3d',
    # Environment
    'park_run_factor', 'wind_mph', 'temperature', 'is_dome',
    # Injuries
    'home_injury_count', 'away_injury_count',
    # Market
    'close_total', 'close_spread',
    'open_total', 'open_spread',
    'home_ml_close', 'away_ml_close',
    # Our built signals
    'nrfi_score',
    'signal_confluence_net',
]


def engineer_features(g, feat_list):
    feat = {k: _f(g.get(k)) for k in feat_list}
    # Convert booleans (is_dome) to 0/1
    if feat.get('is_dome') is not None:
        feat['is_dome'] = 1.0 if g.get('is_dome') else 0.0
    # Add a few interaction features that the kitchen sink would benefit from
    hx, ax = feat.get('home_sp_xera'), feat.get('away_sp_xera')
    if hx is not None and ax is not None:
        feat['xera_gap'] = abs(hx - ax)
        feat['xera_sum'] = hx + ax
    hw = feat.get('home_wrc_vs_opp_hand') or feat.get('home_wrc_plus')
    aw = feat.get('away_wrc_vs_opp_hand') or feat.get('away_wrc_plus')
    if hw is not None and aw is not None:
        feat['wrc_diff'] = hw - aw
        feat['wrc_sum'] = hw + aw
    # Recency vs season divergence
    hr_l10 = feat.get('home_last10_runs_per_game')
    hr_szn = feat.get('home_runs_per_game')
    if hr_l10 is not None and hr_szn is not None:
        feat['home_recency_drift'] = hr_l10 - hr_szn
    ar_l10 = feat.get('away_last10_runs_per_game')
    ar_szn = feat.get('away_runs_per_game')
    if ar_l10 is not None and ar_szn is not None:
        feat['away_recency_drift'] = ar_l10 - ar_szn
    # Max 1st-inn ERA (proxy for fragile-starter risk)
    h1 = feat.get('home_first_inning_era')
    a1 = feat.get('away_first_inning_era')
    if h1 is not None and a1 is not None:
        feat['max_1st_inn_era'] = max(h1, a1)
    # Bullpen fatigue sum
    h_bp = feat.get('home_bp_relievers_3d')
    a_bp = feat.get('away_bp_relievers_3d')
    if h_bp is not None and a_bp is not None:
        feat['bp_fatigue'] = h_bp + a_bp
    return feat


def v3_formula(g):
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


def build_X(games, feat_list):
    # Get the union of all keys engineered across the whole dataset so the
    # schema is consistent. engineered features (xera_gap, wrc_diff, etc.)
    # only appear when inputs are non-null per game — without unifying we
    # get inhomogeneous shapes.
    all_keys = set()
    engineered_per_game = []
    for g in games:
        f = engineer_features(g, feat_list)
        engineered_per_game.append(f)
        all_keys.update(f.keys())
    keys_sorted = sorted(all_keys)
    rows = []
    for f in engineered_per_game:
        row = [float('nan') if f.get(k) is None else float(f[k]) for k in keys_sorted]
        rows.append(row)
    return np.array(rows, dtype=np.float32), keys_sorted


def evaluate(games, WARMUP=200):
    print(f'\n=== Comprehensive eval, warmup {WARMUP}, total {len(games)} ===\n')

    # Must have scores + at least xERA for the formula baseline
    valid = [g for g in games
             if g.get('home_score') is not None and g.get('away_score') is not None
             and _f(g.get('home_sp_xera')) is not None and _f(g.get('away_sp_xera')) is not None]
    print(f'  Games with scores + xERA: {len(valid)}')

    if len(valid) < WARMUP + 30:
        print('  Not enough data')
        return

    actuals_h = np.array([g['home_score'] for g in valid], dtype=np.float32)
    actuals_a = np.array([g['away_score'] for g in valid], dtype=np.float32)
    actuals_diff = actuals_h - actuals_a
    actuals_total = actuals_h + actuals_a
    actuals_home_win = (actuals_diff > 0).astype(np.int32)
    close_totals = np.array([_f(g.get('close_total')) or np.nan for g in valid], dtype=np.float32)
    over_labels = (actuals_total > close_totals).astype(np.int32)

    n = len(valid)

    import xgboost as xgb

    # --- Pre-compute v3 formula ---
    v3_h = np.full(n, np.nan); v3_a = np.full(n, np.nan)
    for i, g in enumerate(valid):
        h, a = v3_formula(g)
        if h is not None:
            v3_h[i] = h; v3_a[i] = a

    # --- Build feature matrices ---
    X_min, feat_names_min = build_X(valid, FEATURES_MINIMAL)
    X_ks, feat_names_ks = build_X(valid, FEATURES_KITCHEN_SINK)
    print(f'  Minimal features: {X_min.shape[1]} cols')
    print(f'  Kitchen-sink features: {X_ks.shape[1]} cols')

    # Predictions
    preds = {
        'v3_formula': {'h': v3_h, 'a': v3_a},
        'xgb_min': {'h': np.full(n, np.nan), 'a': np.full(n, np.nan)},
        'xgb_ks': {'h': np.full(n, np.nan), 'a': np.full(n, np.nan)},
        'xgb_ks_recency': {'h': np.full(n, np.nan), 'a': np.full(n, np.nan)},
        'xgb_ml_classifier': {'home_win_prob': np.full(n, np.nan)},
        'xgb_total_classifier': {'over_prob': np.full(n, np.nan)},
    }

    def make_xgb_reg(big=False):
        if big:
            return xgb.XGBRegressor(
                n_estimators=200, max_depth=4, learning_rate=0.03,
                subsample=0.8, colsample_bytree=0.7,
                reg_alpha=1.0, reg_lambda=2.0, min_child_weight=5,
                random_state=42, verbosity=0, tree_method='hist',
            )
        return xgb.XGBRegressor(
            n_estimators=120, max_depth=3, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.8,
            reg_alpha=0.5, reg_lambda=1.0, min_child_weight=4,
            random_state=42, verbosity=0, tree_method='hist',
        )

    def make_xgb_clf():
        return xgb.XGBClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.03,
            subsample=0.8, colsample_bytree=0.7,
            reg_alpha=1.0, reg_lambda=2.0,
            random_state=42, verbosity=0, tree_method='hist',
        )

    RETRAIN_EVERY = 30
    last_train = None
    M = {}

    print('  Walk-forward training (retrain every 30 games)...')
    for i in range(WARMUP, n):
        if last_train is None or (i - last_train) >= RETRAIN_EVERY:
            # Minimal feature regressor
            mh = make_xgb_reg(); mh.fit(X_min[:i], actuals_h[:i])
            ma = make_xgb_reg(); ma.fit(X_min[:i], actuals_a[:i])
            M['min_h'] = mh; M['min_a'] = ma

            # Kitchen-sink regressor
            kh = make_xgb_reg(big=True); kh.fit(X_ks[:i], actuals_h[:i])
            ka = make_xgb_reg(big=True); ka.fit(X_ks[:i], actuals_a[:i])
            M['ks_h'] = kh; M['ks_a'] = ka

            # Kitchen-sink + recency
            w = np.ones(i, dtype=np.float32)
            cut = int(i * 0.7)
            w[cut:] = 2.5
            krh = make_xgb_reg(big=True); krh.fit(X_ks[:i], actuals_h[:i], sample_weight=w)
            kra = make_xgb_reg(big=True); kra.fit(X_ks[:i], actuals_a[:i], sample_weight=w)
            M['ksr_h'] = krh; M['ksr_a'] = kra

            # ML classifier (home_win binary)
            mlc = make_xgb_clf(); mlc.fit(X_ks[:i], actuals_home_win[:i])
            M['mlc'] = mlc

            # Total classifier (over/under binary)
            valid_ct = ~np.isnan(close_totals[:i]) & (actuals_total[:i] != close_totals[:i])
            if valid_ct.sum() > 100:
                tc = make_xgb_clf()
                tc.fit(X_ks[:i][valid_ct], over_labels[:i][valid_ct])
                M['tc'] = tc

            last_train = i

        xi_min = X_min[i:i+1]
        xi_ks = X_ks[i:i+1]
        preds['xgb_min']['h'][i] = M['min_h'].predict(xi_min)[0]
        preds['xgb_min']['a'][i] = M['min_a'].predict(xi_min)[0]
        preds['xgb_ks']['h'][i] = M['ks_h'].predict(xi_ks)[0]
        preds['xgb_ks']['a'][i] = M['ks_a'].predict(xi_ks)[0]
        preds['xgb_ks_recency']['h'][i] = M['ksr_h'].predict(xi_ks)[0]
        preds['xgb_ks_recency']['a'][i] = M['ksr_a'].predict(xi_ks)[0]
        preds['xgb_ml_classifier']['home_win_prob'][i] = M['mlc'].predict_proba(xi_ks)[0][1]
        if 'tc' in M and not np.isnan(close_totals[i]):
            preds['xgb_total_classifier']['over_prob'][i] = M['tc'].predict_proba(xi_ks)[0][1]

    # --- Report ---
    print(f'\n{"="*78}')
    print('RESULTS')
    print('='*78)

    def evaluate_reg(name, h, a):
        tot = h + a
        diff = h - a
        # Total MAE
        m = ~np.isnan(tot) & ~np.isnan(actuals_total)
        tot_mae = np.mean(np.abs(tot[m] - actuals_total[m])) if m.any() else float('nan')
        # Spread direction accuracy (who wins)
        m2 = ~np.isnan(diff) & ~np.isnan(actuals_diff)
        if m2.any():
            dir_acc = ((diff[m2] > 0) == (actuals_diff[m2] > 0)).mean() * 100
        else:
            dir_acc = float('nan')
        # ML direction accuracy at confidence buckets
        return tot_mae, dir_acc, m2.sum()

    print(f'\n{"Model":<22} {"TotMAE":<8} {"DirAcc":<8} {"n"}')
    print('-' * 50)
    for k in ('v3_formula', 'xgb_min', 'xgb_ks', 'xgb_ks_recency'):
        tm, da, nn = evaluate_reg(k, preds[k]['h'], preds[k]['a'])
        print(f'{k:<22} {tm:<8.3f} {da:<8.2f} {nn}')

    # ML classifier (binary home_win prediction)
    print('\n--- ML classifier (predicts home_win directly) ---')
    p = preds['xgb_ml_classifier']['home_win_prob']
    valid_mask = ~np.isnan(p)
    if valid_mask.any():
        # Overall accuracy
        pred_win = (p[valid_mask] > 0.5).astype(int)
        actual = actuals_home_win[valid_mask]
        acc = (pred_win == actual).mean() * 100
        print(f'  Overall: {acc:.2f}%  n={valid_mask.sum()}')
        # By confidence bucket
        for thresh in [0.55, 0.6, 0.65, 0.7]:
            high_conf = (p[valid_mask] >= thresh) | (p[valid_mask] <= 1-thresh)
            if high_conf.sum() > 10:
                hc_pred = (p[valid_mask][high_conf] > 0.5).astype(int)
                hc_actual = actual[high_conf]
                hc_acc = (hc_pred == hc_actual).mean() * 100
                print(f'  At conf ≥{int(thresh*100)}%: {hc_acc:.1f}%  n={high_conf.sum()}')

    # Total classifier
    print('\n--- Total classifier (predicts OVER directly) ---')
    p = preds['xgb_total_classifier']['over_prob']
    valid_mask = ~np.isnan(p) & ~np.isnan(close_totals) & (actuals_total != close_totals)
    if valid_mask.any():
        pred_over = (p[valid_mask] > 0.5).astype(int)
        actual = (actuals_total[valid_mask] > close_totals[valid_mask]).astype(int)
        acc = (pred_over == actual).mean() * 100
        print(f'  Overall: {acc:.2f}%  n={valid_mask.sum()}')
        for thresh in [0.55, 0.6, 0.65, 0.7]:
            high_conf = (p[valid_mask] >= thresh) | (p[valid_mask] <= 1-thresh)
            if high_conf.sum() > 10:
                hc_pred = (p[valid_mask][high_conf] > 0.5).astype(int)
                hc_actual = actual[high_conf]
                hc_acc = (hc_pred == hc_actual).mean() * 100
                print(f'  At conf ≥{int(thresh*100)}%: {hc_acc:.1f}%  n={high_conf.sum()}')

    # Subset analysis: where IS the model predictive?
    print('\n--- Subset analysis (kitchen-sink regressor) ---')
    h, a = preds['xgb_ks']['h'], preds['xgb_ks']['a']
    diff = h - a
    base_mask = ~np.isnan(diff) & ~np.isnan(actuals_diff)

    # High confluence games
    conf_arr = np.array([_f(g.get('signal_confluence_net')) or 0 for g in valid])
    masks_to_test = [
        ('|confluence_net| ≥ 4', base_mask & (np.abs(conf_arr) >= 4)),
        ('|confluence_net| ≥ 5', base_mask & (np.abs(conf_arr) >= 5)),
        ('Both bullpens fresh (≤6)', base_mask & np.array([
            (_f(g.get('home_bp_relievers_3d')) or 0) <= 6 and (_f(g.get('away_bp_relievers_3d')) or 0) <= 6
            for g in valid
        ])),
        ('Both lineups confirmed', base_mask & np.array([
            bool(g.get('lineup_confirmed')) for g in valid
        ])),
        ('Domed game', base_mask & np.array([bool(g.get('is_dome')) for g in valid])),
        ('No starter L3 >7 (clean inputs)', base_mask & np.array([
            (_f(g.get('home_pitcher_last_3_era')) or 0) < 7
            and (_f(g.get('away_pitcher_last_3_era')) or 0) < 7
            for g in valid
        ])),
    ]
    for label, mask in masks_to_test:
        if mask.sum() > 20:
            sub_acc = ((diff[mask] > 0) == (actuals_diff[mask] > 0)).mean() * 100
            print(f'  {label:<35} {sub_acc:.1f}%  n={mask.sum()}')

    # Feature importance from kitchen-sink regressor
    print('\n--- Top 15 feature importances (kitchen-sink home model, last training) ---')
    if 'ks_h' in M:
        import_arr = M['ks_h'].feature_importances_
        top = sorted(zip(feat_names_ks, import_arr), key=lambda x: -x[1])[:15]
        for fn, imp in top:
            print(f'  {fn:<35} {imp:.4f}')

    return preds


if __name__ == '__main__':
    games = fetch_games()
    evaluate(games)
