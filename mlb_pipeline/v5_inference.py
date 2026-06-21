"""v5 inference — loads v5_ml + v5_total and produces probabilities for a game.

Usage from play_of_day or other selectors:

    from v5_inference import predict_ml, predict_total
    p_home = predict_ml(game_context)        # 0..1, prob home wins
    p_over = predict_total(game_context)     # 0..1, prob OVER

Both functions take an mlb_game_context dict (or any dict with the trained
features) and return None when the feature stack can't be assembled (e.g.
v4 model not generated, missing xERA).

Confidence interpretation (from 14-day holdout validation):
  v5_ml  : |prob-0.5|>=0.05 hits 64% (n=62), >=0.10 hits 75% (n=8)
  v5_total: |prob-0.5|>=0.05 hits 71% (n=42), >=0.10 hits 95% (n=20)

So v5 is best used as a CONFIDENCE-GATED FILTER on top of the existing
selector — not as a primary predictor. When v5 disagrees with v3/v4/jerry
in high confidence, that's a strong fade signal for the consensus.

Model files: mlb_pipeline/models/v5_ml.json, v5_total.json
Meta files: same dir, *_meta.json (contains feature order + medians for
NaN-fill consistency between train and inference).
"""
import json
import os
from pathlib import Path
from typing import Optional

try:
    import numpy as np
    from xgboost import XGBClassifier
    _XGB_OK = True
except Exception:
    _XGB_OK = False

_MODELS_DIR = Path(__file__).parent / 'models'
_ML_MODEL = None
_ML_META = None
_TOT_MODEL = None
_TOT_META = None


def _load_ml():
    """Lazy-load v5_ml the first time it's called. Returns (model, meta) or
    (None, None) when the model file isn't present yet (training hasn't run
    or this is a clean checkout)."""
    global _ML_MODEL, _ML_META
    if _ML_MODEL is not None:
        return _ML_MODEL, _ML_META
    if not _XGB_OK:
        return None, None
    model_path = _MODELS_DIR / 'v5_ml.json'
    meta_path = _MODELS_DIR / 'v5_ml_meta.json'
    if not model_path.exists() or not meta_path.exists():
        return None, None
    clf = XGBClassifier()
    clf.load_model(str(model_path))
    with open(meta_path) as f:
        meta = json.load(f)
    _ML_MODEL = clf
    _ML_META = meta
    return clf, meta


def _load_total():
    global _TOT_MODEL, _TOT_META
    if _TOT_MODEL is not None:
        return _TOT_MODEL, _TOT_META
    if not _XGB_OK:
        return None, None
    model_path = _MODELS_DIR / 'v5_total.json'
    meta_path = _MODELS_DIR / 'v5_total_meta.json'
    if not model_path.exists() or not meta_path.exists():
        return None, None
    clf = XGBClassifier()
    clf.load_model(str(model_path))
    with open(meta_path) as f:
        meta = json.load(f)
    _TOT_MODEL = clf
    _TOT_META = meta
    return clf, meta


def _row_from_ctx(ctx: dict, features: list, medians: dict):
    """Build the feature row in trained-feature order, NaN-fill with the
    same medians used at train time so train/inference are aligned. Returns
    None when ctx is empty / not a dict; otherwise always a usable row
    (median-filled features keep the model functional even on incomplete
    pre-game data)."""
    if not isinstance(ctx, dict):
        return None
    row = []
    for f in features:
        v = ctx.get(f)
        try:
            x = float(v) if v is not None else float('nan')
        except (TypeError, ValueError):
            x = float('nan')
        if x != x:  # NaN
            x = float(medians.get(f, 0.0))
        row.append(x)
    return np.array([row], dtype=float)


def predict_ml(ctx: dict) -> Optional[float]:
    """Returns P(home_win) in [0,1] or None when model not available."""
    clf, meta = _load_ml()
    if clf is None or meta is None:
        return None
    row = _row_from_ctx(ctx, meta['features'], meta.get('feature_medians', {}))
    if row is None:
        return None
    return float(clf.predict_proba(row)[0, 1])


def predict_total(ctx: dict) -> Optional[float]:
    """Returns P(OVER) in [0,1] or None when model not available."""
    clf, meta = _load_total()
    if clf is None or meta is None:
        return None
    row = _row_from_ctx(ctx, meta['features'], meta.get('feature_medians', {}))
    if row is None:
        return None
    return float(clf.predict_proba(row)[0, 1])


def confidence_tier(prob: Optional[float]) -> str:
    """Translate raw probability into a tier label aligned with the
    holdout-validated confidence bands.

    v5 ML bands: |prob-0.5|>=0.05 hit 64% / >=0.10 hit 75% on holdout
    v5 TOT bands: |prob-0.5|>=0.05 hit 71% / >=0.10 hit 95% on holdout
    """
    if prob is None:
        return 'UNAVAILABLE'
    dev = abs(prob - 0.5)
    if dev >= 0.15:
        return 'ELITE'
    if dev >= 0.10:
        return 'STRONG'
    if dev >= 0.05:
        return 'LEAN'
    return 'PASS'


if __name__ == '__main__':
    # Quick smoke test against today's games
    import os, requests
    from dotenv import load_dotenv
    load_dotenv()
    SU = os.environ.get('SUPABASE_URL'); SK = os.environ.get('SUPABASE_KEY')
    if SU and SK:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_context?game_date=eq.2026-06-21&select=*',
            headers={'apikey': SK, 'Authorization': f'Bearer {SK}'},
            timeout=15,
        )
        games = r.json()
        print(f'{"Game":40s} {"v5 ML":>10s} {"tier":>8s} {"v5 TOT":>10s} {"tier":>8s}')
        print('-' * 90)
        for g in games:
            if not isinstance(g, dict):
                continue
            name = f"{(g.get('away_team') or '')[:18]} @ {(g.get('home_team') or '')[:18]}"
            p_ml = predict_ml(g)
            p_tot = predict_total(g)
            ml_str = f'{p_ml*100:.0f}% H' if p_ml is not None else '-'
            tot_str = f'{p_tot*100:.0f}% O' if p_tot is not None else '-'
            ml_tier = confidence_tier(p_ml)
            tot_tier = confidence_tier(p_tot)
            print(f'  {name[:38]:38s} {ml_str:>10s} {ml_tier:>8s} {tot_str:>10s} {tot_tier:>8s}')
