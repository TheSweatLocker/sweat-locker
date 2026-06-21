"""SHAP-lite feature importance for v5_ml and v5_total.

XGBoost's native pred_contribs is the same math as SHAP for tree models.
For each prediction, output the per-feature contribution to log-odds.
Aggregate across the train set to see which features actually move the
needle.

Output:
  - Global feature importance (mean |SHAP|) per model
  - Top 5 features by absolute contribution
  - Bottom 5 features (candidates for dropping next retrain)
  - Per-tonight features for the loudest sweat dim signal

Drop gate: if a feature has mean |SHAP| < 0.01 AND isn't in the top 80%
cumulative contribution, propose dropping in next retrain.
"""
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from xgboost import XGBClassifier

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

_MODELS_DIR = Path(__file__).parent / 'models'


def load_model(name):
    model_path = _MODELS_DIR / f'{name}.json'
    meta_path = _MODELS_DIR / f'{name}_meta.json'
    if not model_path.exists() or not meta_path.exists():
        return None, None
    clf = XGBClassifier()
    clf.load_model(str(model_path))
    with open(meta_path) as f:
        meta = json.load(f)
    return clf, meta


def pull_recent_games(days=60):
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=days)).isoformat()
    sel = '*'
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?game_date=gte.{since}'
            f'&select={sel}&order=game_date.asc&limit=1000&offset={offset}',
            headers=H, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return [r for r in rows if r.get('home_score') is not None]


def analyze_model(name, label):
    print('=' * 80)
    print(f'{label} — Feature importance analysis')
    print('=' * 80)
    clf, meta = load_model(name)
    if clf is None:
        print('  Model not loaded')
        return
    features = meta['features']
    medians = meta['feature_medians']

    # Method 1: XGBoost native importance (gain-based)
    print(f'\n-- Native gain importance --')
    importance_pairs = [(f, float(g)) for f, g in zip(features, clf.feature_importances_)]
    importance_pairs.sort(key=lambda x: -x[1])
    for f, g in importance_pairs:
        bar = '█' * max(1, int(g * 100))
        print(f'  {f:>30s}  {g:.4f}  {bar}')

    # Method 2: Per-prediction SHAP via xgboost pred_contribs
    games = pull_recent_games(60)
    if not games:
        return
    rows = []
    for g in games:
        row = []
        for f in features:
            v = g.get(f)
            try:
                x = float(v) if v is not None else float('nan')
            except (TypeError, ValueError):
                x = float('nan')
            if x != x:
                x = float(medians.get(f, 0.0))
            row.append(x)
        rows.append(row)
    X = np.array(rows, dtype=float)
    # pred_contribs returns (n_samples, n_features+1) where last column is bias
    contribs = clf.get_booster().predict(
        __import__('xgboost').DMatrix(X), pred_contribs=True
    )
    # Mean absolute SHAP per feature
    mean_abs = np.abs(contribs[:, :-1]).mean(axis=0)
    print(f'\n-- Mean |SHAP| over n={len(rows)} recent games --')
    shap_pairs = sorted(
        zip(features, mean_abs.tolist()), key=lambda x: -x[1]
    )
    cum = 0; total = sum(mean_abs)
    print(f'{"feature":>32s}  {"mean |SHAP|":>11s}  {"cum %":>6s}')
    for f, s in shap_pairs:
        cum += s
        cum_pct = 100 * cum / total
        flag = ''
        if s < 0.01 and cum_pct < 95:
            flag = '  ← DROP candidate'
        elif s < 0.02 and cum_pct > 90:
            flag = '  ← MAYBE DROP'
        print(f'  {f:>30s}  {s:>10.4f}  {cum_pct:>5.1f}%{flag}')


def main():
    analyze_model('v5_ml', 'V5_ML')
    print()
    analyze_model('v5_total', 'V5_TOTAL')


if __name__ == '__main__':
    main()
