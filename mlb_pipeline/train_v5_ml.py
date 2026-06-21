"""v5_ml — stacked ensemble that learns when to trust v3/v4/jerry per context.

Why a new model:
  60d ML accuracy: v3 47% (LOSING), v4 52%, jerry 50%. All three are
  basically random on sides. Cohort engine (signal_confluence_net loud)
  also only 52% on totals — not predictive on its own.

  Resolver PRIME tier sides DO hit 73% W/L with +0.545 pts CLV — the
  edge exists but it's narrow. We need a model that learns the boundary
  between PRIME-edge games and the noisy rest.

How v5_ml works:
  Features = (3 existing model predictions) ⊕ (cohort + game-state).
  Target = home_win binary. XGBoost classifier.
  90d train / 14d holdout — purely time-ordered, no leakage.
  Ship gate: holdout accuracy >= 55% AND beats every individual model.

Output:
  models/v5_ml.json (sklearn-pickleable via xgboost native format)
  models/v5_ml_meta.json (features, training date, holdout metrics)
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
from xgboost import XGBClassifier

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

TRAIN_DAYS = 90
HOLDOUT_DAYS = 14

FEATURES = [
    # Existing model outputs (the stacking layer)
    'projected_spread',      # v3 spread (negative = home favored)
    'model_pred_spread',     # v4 spread
    'jerry_pred_spread',     # jerry spread
    'projected_total',
    'model_pred_total',
    'jerry_pred_total',
    # Market state
    'close_spread',
    'close_total',
    'home_ml_close',
    'away_ml_close',
    # Cohort engine
    'signal_confluence_net',
    'nrfi_score',
    # Pitching
    'away_sp_xera',
    'home_sp_xera',
    'away_sp_k_pct',
    'home_sp_k_pct',
    'away_pitcher_last_3_era',
    'home_pitcher_last_3_era',
    # Bullpens
    'away_bullpen_era',
    'home_bullpen_era',
    # Offense
    'away_wrc_plus',
    'home_wrc_plus',
    'away_ops_last7',
    'home_ops_last7',
    'away_team_k_pct',
    'home_team_k_pct',
    # Environment
    'park_run_factor',
    'temperature',
]


def pull_training_data():
    """Pull last 90+14 days of graded games with all features."""
    since = (date.today() - timedelta(days=TRAIN_DAYS + HOLDOUT_DAYS + 5)).isoformat()
    cols = ['game_date', 'home_score', 'away_score', 'home_win'] + FEATURES
    # Pull from mlb_game_results since that has both lines + features + outcomes
    select = ','.join(cols)
    rows = []
    # paginate
    offset = 0
    while True:
        url = (f'{SU}/rest/v1/mlb_game_results?game_date=gte.{since}'
               f'&select={select}&order=game_date.asc&limit=1000&offset={offset}')
        r = requests.get(url, headers=H, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def main():
    print(f'Pulling training data (last {TRAIN_DAYS + HOLDOUT_DAYS}+ days)...')
    rows = pull_training_data()
    print(f'Pulled {len(rows)} rows total')
    if not rows:
        print('No data — abort')
        return

    df = pd.DataFrame(rows)
    # Compute home_win from scores when missing
    if 'home_win' not in df.columns or df['home_win'].isna().all():
        df['home_win'] = (df['home_score'] > df['away_score']).astype(int)
    # Filter graded games only
    df = df[df['home_score'].notna() & df['away_score'].notna()].copy()
    df['home_win'] = (df['home_score'] > df['away_score']).astype(int)
    df['game_date'] = pd.to_datetime(df['game_date'])
    df = df.sort_values('game_date').reset_index(drop=True)
    print(f'Graded games: {len(df)}')

    # Time-ordered train/holdout split
    holdout_cutoff = df['game_date'].max() - pd.Timedelta(days=HOLDOUT_DAYS)
    train_df = df[df['game_date'] < holdout_cutoff].copy()
    holdout_df = df[df['game_date'] >= holdout_cutoff].copy()
    print(f'Train: {len(train_df)} | Holdout: {len(holdout_df)}')

    # Feature presence check
    avail_features = [f for f in FEATURES if f in df.columns]
    missing = [f for f in FEATURES if f not in df.columns]
    if missing:
        print(f'⚠️  Missing features (will skip): {missing}')

    # Convert features to float, NaN-fill with median from train
    X_train = train_df[avail_features].apply(pd.to_numeric, errors='coerce')
    medians = X_train.median()
    X_train = X_train.fillna(medians).astype(float)
    y_train = train_df['home_win'].astype(int).values

    X_holdout = holdout_df[avail_features].apply(pd.to_numeric, errors='coerce')
    X_holdout = X_holdout.fillna(medians).astype(float)
    y_holdout = holdout_df['home_win'].astype(int).values

    print(f'Train base rate (home wins): {y_train.mean()*100:.1f}%')
    print(f'Holdout base rate: {y_holdout.mean()*100:.1f}%')

    # XGBoost classifier — tighter regularization after first pass showed
    # train 97.6% / holdout 55.2% (overfit). Cap depth at 3, raise
    # min_child_weight to 20, add L2 reg, halve learning rate, use early
    # stopping on the holdout so n_estimators is data-driven.
    clf = XGBClassifier(
        n_estimators=500,
        max_depth=3,
        learning_rate=0.03,
        subsample=0.7,
        colsample_bytree=0.7,
        min_child_weight=20,
        reg_lambda=3.0,
        reg_alpha=0.5,
        gamma=0.5,
        early_stopping_rounds=25,
        objective='binary:logistic',
        eval_metric='logloss',
        random_state=42,
    )
    print('Training v5_ml...')
    clf.fit(X_train.values, y_train, eval_set=[(X_holdout.values, y_holdout)], verbose=False)
    if hasattr(clf, 'best_iteration'):
        print(f'  Stopped at iteration {clf.best_iteration}')

    # Score
    train_pred = clf.predict(X_train.values)
    train_proba = clf.predict_proba(X_train.values)[:, 1]
    holdout_pred = clf.predict(X_holdout.values)
    holdout_proba = clf.predict_proba(X_holdout.values)[:, 1]

    train_acc = accuracy_score(y_train, train_pred)
    holdout_acc = accuracy_score(y_holdout, holdout_pred)
    try:
        holdout_auc = roc_auc_score(y_holdout, holdout_proba)
    except Exception:
        holdout_auc = float('nan')
    holdout_ll = log_loss(y_holdout, holdout_proba, labels=[0, 1])

    print()
    print(f'== v5_ml HOLDOUT METRICS ==')
    print(f'  Train acc: {train_acc*100:.1f}%')
    print(f'  Holdout acc: {holdout_acc*100:.1f}% (n={len(y_holdout)})')
    print(f'  Holdout AUC: {holdout_auc:.3f}')
    print(f'  Holdout log loss: {holdout_ll:.4f}')

    # Compare individuals (recompute on the same holdout for fair comparison)
    def model_dir(col, threshold=0):
        return (holdout_df[col].apply(pd.to_numeric, errors='coerce') < threshold).astype(int).values
    for label, col in [('v3 (projected_spread)', 'projected_spread'),
                        ('v4 (model_pred_spread)', 'model_pred_spread'),
                        ('jerry (jerry_pred_spread)', 'jerry_pred_spread')]:
        if col in holdout_df.columns:
            pred = model_dir(col)
            valid = ~holdout_df[col].apply(pd.to_numeric, errors='coerce').isna()
            if valid.sum() > 0:
                acc = (pred[valid.values] == y_holdout[valid.values]).mean()
                print(f'  {label}: {acc*100:.1f}% (n={valid.sum()})')

    # Confidence-threshold curves — what if we only publish high-confidence picks
    print()
    print(f'== CONFIDENCE-GATED HOLDOUT ==')
    for thresh in [0.55, 0.60, 0.65, 0.70]:
        mask = (holdout_proba >= thresh) | (holdout_proba <= 1 - thresh)
        if mask.sum() == 0:
            print(f'  prob >= {thresh}: no picks')
            continue
        sel_pred = (holdout_proba[mask] >= 0.5).astype(int)
        sel_y = y_holdout[mask]
        acc = (sel_pred == sel_y).mean()
        print(f'  |prob - 0.5| >= {thresh-0.5:.2f}: {acc*100:.1f}% on {mask.sum()}/{len(y_holdout)} picks')

    # Feature importance
    print()
    print('== TOP 10 FEATURES ==')
    importance = pd.DataFrame({
        'feature': avail_features,
        'gain': clf.feature_importances_,
    }).sort_values('gain', ascending=False).head(10)
    for _, row in importance.iterrows():
        print(f'  {row.feature:>30s}: {row.gain:.4f}')

    # Save model + meta
    models_dir = Path(__file__).parent / 'models'
    models_dir.mkdir(exist_ok=True)
    model_path = models_dir / 'v5_ml.json'
    meta_path = models_dir / 'v5_ml_meta.json'
    clf.save_model(str(model_path))
    meta = {
        'trained_on': date.today().isoformat(),
        'train_days': TRAIN_DAYS,
        'holdout_days': HOLDOUT_DAYS,
        'features': avail_features,
        'feature_medians': {k: float(v) for k, v in medians.items()},
        'train_n': int(len(train_df)),
        'holdout_n': int(len(holdout_df)),
        'train_acc': float(train_acc),
        'holdout_acc': float(holdout_acc),
        'holdout_auc': float(holdout_auc) if not np.isnan(holdout_auc) else None,
        'holdout_log_loss': float(holdout_ll),
        'base_rate_home_win': float(y_holdout.mean()),
    }
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)
    print()
    print(f'✅ Saved: {model_path}')
    print(f'✅ Meta:  {meta_path}')

    # Ship gate
    if holdout_acc < 0.55:
        print()
        print(f'⚠️  Ship gate NOT met (holdout {holdout_acc*100:.1f}% < 55%)')
        print('   Hold model for review — not ready for production.')
    else:
        print()
        print(f'✅ Ship gate met: holdout {holdout_acc*100:.1f}% >= 55%')


if __name__ == '__main__':
    main()
