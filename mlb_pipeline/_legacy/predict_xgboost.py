"""
XGBoost prediction helpers — load trained models and predict on today's games.

Used by game_context.py pipeline to add ML predictions alongside the
rule-based models. Runs ONLY if models exist in mlb_pipeline/models/.

Usage in pipeline:
  from predict_xgboost import predict_game
  ml_preds = predict_game(context)  # returns {'nrfi_prob': 0.68, 'total_pred': 8.4, 'home_win_prob': 0.57}

If models haven't been trained yet, returns None. Pipeline continues without ML.
"""
import os
import pickle

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')

_loaded_models = None  # cache

def _load_models():
    """Lazy-load models once per pipeline run"""
    global _loaded_models
    if _loaded_models is not None:
        return _loaded_models

    models = {}
    for name, filename in [('nrfi', 'nrfi_model.pkl'), ('total', 'total_model.pkl'), ('home_win', 'home_win_model.pkl')]:
        path = os.path.join(MODELS_DIR, filename)
        if os.path.exists(path):
            try:
                with open(path, 'rb') as f:
                    models[name] = pickle.load(f)
            except Exception as e:
                print(f'  ML model load failed for {name}: {e}')
    _loaded_models = models if models else {}
    return _loaded_models

def predict_game(context):
    """
    Given a game context dict (from pipeline), return ML predictions.
    Returns None if no models are available yet.
    """
    models = _load_models()
    if not models:
        return None

    import numpy as np

    def build_feature_vector(model_bundle, ctx):
        features = model_bundle['features']
        row = []
        for f in features:
            v = ctx.get(f)
            try:
                row.append(float(v) if v is not None else 0.0)
            except:
                row.append(0.0)
        return np.array([row])

    result = {}
    if 'nrfi' in models:
        try:
            X = build_feature_vector(models['nrfi'], context)
            prob = models['nrfi']['model'].predict_proba(X)[0][1]
            result['nrfi_prob'] = round(float(prob), 3)
        except:
            pass

    if 'total' in models:
        try:
            X = build_feature_vector(models['total'], context)
            pred = models['total']['model'].predict(X)[0]
            result['total_pred'] = round(float(pred), 2)
        except:
            pass

    if 'home_win' in models:
        try:
            X = build_feature_vector(models['home_win'], context)
            prob = models['home_win']['model'].predict_proba(X)[0][1]
            result['home_win_prob'] = round(float(prob), 3)
        except:
            pass

    return result if result else None
