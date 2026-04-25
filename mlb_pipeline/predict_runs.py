"""
Load home_runs and away_runs XGBoost models and predict per-game.

Used by game_context.py to replace the v3 hand-coded formula. If model files
don't exist (.pkl missing), callers should fall back to the formula.

Usage from game_context.py:
    from predict_runs import predict_runs, MODELS_LOADED
    if MODELS_LOADED:
        home_exp, away_exp = predict_runs(feature_dict)

Where feature_dict matches the keys expected by engineer_features() in
train_runs_model.py — same RAW_FEATURES + engineered ones.
"""
import os
import pickle

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
HOME_MODEL_PATH = os.path.join(MODELS_DIR, 'home_runs_model.pkl')
AWAY_MODEL_PATH = os.path.join(MODELS_DIR, 'away_runs_model.pkl')

_HOME_BUNDLE = None
_AWAY_BUNDLE = None
MODELS_LOADED = False


def _load():
    global _HOME_BUNDLE, _AWAY_BUNDLE, MODELS_LOADED
    if not (os.path.exists(HOME_MODEL_PATH) and os.path.exists(AWAY_MODEL_PATH)):
        MODELS_LOADED = False
        return
    try:
        with open(HOME_MODEL_PATH, 'rb') as f:
            _HOME_BUNDLE = pickle.load(f)
        with open(AWAY_MODEL_PATH, 'rb') as f:
            _AWAY_BUNDLE = pickle.load(f)
        # Validate same feature schema
        if _HOME_BUNDLE.get('features') != _AWAY_BUNDLE.get('features'):
            print('  ⚠️ predict_runs: home/away model feature schemas differ — refusing to load')
            MODELS_LOADED = False
            return
        MODELS_LOADED = True
    except Exception as e:
        print(f'  ⚠️ predict_runs: failed to load models: {e}')
        MODELS_LOADED = False


_load()


def get_feature_names():
    """Returns the feature names the models were trained on, in order."""
    if _HOME_BUNDLE is None:
        return []
    return _HOME_BUNDLE.get('features', [])


def get_model_meta():
    """Returns metadata about the loaded models (training date, walk-forward metrics)."""
    if _HOME_BUNDLE is None:
        return None
    return _HOME_BUNDLE.get('meta')


def predict_runs(feature_dict):
    """Predict (home_runs, away_runs) for a single game.

    feature_dict must contain the same keys as RAW_FEATURES + engineered features
    from train_runs_model.engineer_features().

    Returns (home_runs, away_runs) as floats. Returns (None, None) if models
    not loaded or required features all missing.
    """
    if not MODELS_LOADED:
        return None, None
    import numpy as np
    feat_names = _HOME_BUNDLE['features']
    row = []
    for k in feat_names:
        v = feature_dict.get(k)
        try:
            row.append(float('nan') if v is None else float(v))
        except (ValueError, TypeError):
            row.append(float('nan'))
    X = np.array([row], dtype=np.float32)
    h = float(_HOME_BUNDLE['model'].predict(X)[0])
    a = float(_AWAY_BUNDLE['model'].predict(X)[0])
    return h, a


def build_feature_dict(ctx):
    """Build the feature dict from a game-context-style row (used by both training
    and prediction so the schemas stay aligned).

    `ctx` should be a dict with the raw fields as keys (matches mlb_game_context
    column names — same as mlb_game_results).

    Mirrors train_runs_model.engineer_features() exactly. If you change one,
    change the other.
    """
    def _f(v):
        try:
            return float(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    raw_keys = [
        'home_sp_xera', 'away_sp_xera',
        'home_sp_whiff_rate', 'away_sp_whiff_rate',
        'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
        'home_wrc_vs_opp_hand', 'away_wrc_vs_opp_hand',
        'home_woba', 'away_woba',
        'home_runs_per_game', 'away_runs_per_game',
        'home_k_gap', 'away_k_gap',
        'home_lineup_weight', 'away_lineup_weight',
        'park_run_factor', 'wind_mph',
        'close_total', 'close_spread',
        'nrfi_score',
    ]
    feat = {k: _f(ctx.get(k)) for k in raw_keys}

    hx, ax = feat['home_sp_xera'], feat['away_sp_xera']
    feat['xera_gap'] = abs(hx - ax) if hx is not None and ax is not None else None

    hw = feat.get('home_wrc_vs_opp_hand')
    aw = feat.get('away_wrc_vs_opp_hand')
    feat['wrc_hand_diff'] = (hw - aw) if hw is not None and aw is not None else None

    h3, a3 = feat['home_pitcher_last_3_era'], feat['away_pitcher_last_3_era']
    feat['l3_era_diff'] = (h3 - a3) if h3 is not None and a3 is not None else None

    ps = _f(ctx.get('projected_spread'))
    cs = _f(ctx.get('close_spread'))
    feat['corrected_spread_delta'] = (ps + cs) if ps is not None and cs is not None else None
    if ps is not None and cs is not None:
        feat['model_market_agree'] = 1 if (ps > 0) == (cs < 0) else 0
    else:
        feat['model_market_agree'] = None

    pt = _f(ctx.get('projected_total'))
    ct = feat['close_total']
    feat['total_delta'] = (pt - ct) if pt is not None and ct is not None else None

    park, wind = feat.get('park_run_factor'), feat.get('wind_mph')
    feat['park_wind'] = (park * wind) if park is not None and wind is not None else None

    return feat


if __name__ == '__main__':
    # Quick smoke test
    print(f'MODELS_LOADED: {MODELS_LOADED}')
    if MODELS_LOADED:
        meta = get_model_meta()
        print(f"Trained: {meta.get('trained_at')}")
        print(f"N games: {meta.get('n_games')}")
        print(f"Model type: {meta.get('model_type')}")
        print(f"Features ({len(get_feature_names())}): {get_feature_names()}")
        # Sample prediction
        sample = {
            'home_sp_xera': 3.0, 'away_sp_xera': 4.5,
            'home_wrc_vs_opp_hand': 110, 'away_wrc_vs_opp_hand': 95,
            'park_run_factor': 100, 'wind_mph': 5,
            'close_total': 8.5, 'close_spread': -1.5,
            'projected_spread': 1.2, 'projected_total': 8.7,
            'nrfi_score': 75,
        }
        feat = build_feature_dict(sample)
        h, a = predict_runs(feat)
        print(f'\nSample prediction: home {h:.2f}, away {a:.2f}, spread {h-a:+.2f}, total {h+a:.2f}')
