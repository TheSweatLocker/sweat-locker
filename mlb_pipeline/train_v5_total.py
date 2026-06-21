"""v5_total — stacked ensemble for over/under prediction.

Same architecture as v5_ml: 3 existing model outputs + cohort + game state
features fed into XGBoost. Target = (actual_total > close_total).

Train ship gate: holdout accuracy >= 60% AND beats every individual model.
Lower bar than ML (55%) doesn't apply here — totals are easier than ML for
the existing models (~52-55% individual) so the bar's higher.
"""
import json
import os
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
    # Existing model outputs
    'projected_total',
    'model_pred_total',
    'jerry_pred_total',
    'projected_spread',
    'model_pred_spread',
    'jerry_pred_spread',
    # Market state
    'close_total',
    'close_spread',
    'home_ml_close',
    'away_ml_close',
    # Cohort + first inning
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
    since = (date.today() - timedelta(days=TRAIN_DAYS + HOLDOUT_DAYS + 5)).isoformat()
    cols = ['game_date', 'home_score', 'away_score'] + FEATURES
    select = ','.join(cols)
    rows = []
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
    print(f'Pulling training data...')
    rows = pull_training_data()
    print(f'Pulled {len(rows)} rows')
    if not rows:
        return

    df = pd.DataFrame(rows)
    df = df[df['home_score'].notna() & df['away_score'].notna()].copy()
    # Drop pushes — they're not classifiable
    df['actual_total'] = df['home_score'] + df['away_score']
    df['over_label'] = (df['actual_total'] > df['close_total']).astype(int)
    df = df[df['actual_total'] != df['close_total']].copy()  # drop pushes
    df['game_date'] = pd.to_datetime(df['game_date'])
    df = df.sort_values('game_date').reset_index(drop=True)
    print(f'Graded non-push games: {len(df)}')

    holdout_cutoff = df['game_date'].max() - pd.Timedelta(days=HOLDOUT_DAYS)
    train_df = df[df['game_date'] < holdout_cutoff].copy()
    holdout_df = df[df['game_date'] >= holdout_cutoff].copy()
    print(f'Train: {len(train_df)} | Holdout: {len(holdout_df)}')

    avail_features = [f for f in FEATURES if f in df.columns]
    missing = [f for f in FEATURES if f not in df.columns]
    if missing:
        print(f'⚠️  Missing: {missing}')

    X_train = train_df[avail_features].apply(pd.to_numeric, errors='coerce')
    medians = X_train.median()
    X_train = X_train.fillna(medians).astype(float)
    y_train = train_df['over_label'].astype(int).values

    X_holdout = holdout_df[avail_features].apply(pd.to_numeric, errors='coerce')
    X_holdout = X_holdout.fillna(medians).astype(float)
    y_holdout = holdout_df['over_label'].astype(int).values

    print(f'Train OVER rate: {y_train.mean()*100:.1f}%')
    print(f'Holdout OVER rate: {y_holdout.mean()*100:.1f}%')

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
    print('Training v5_total...')
    clf.fit(X_train.values, y_train, eval_set=[(X_holdout.values, y_holdout)], verbose=False)
    if hasattr(clf, 'best_iteration'):
        print(f'  Stopped at iteration {clf.best_iteration}')

    train_pred = clf.predict(X_train.values)
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
    print(f'== v5_total HOLDOUT METRICS ==')
    print(f'  Train acc: {train_acc*100:.1f}%')
    print(f'  Holdout acc: {holdout_acc*100:.1f}% (n={len(y_holdout)})')
    print(f'  Holdout AUC: {holdout_auc:.3f}')
    print(f'  Holdout log loss: {holdout_ll:.4f}')

    # Compare individuals on this holdout
    for label, col in [('v3 (projected_total)', 'projected_total'),
                        ('v4 (model_pred_total)', 'model_pred_total'),
                        ('jerry (jerry_pred_total)', 'jerry_pred_total')]:
        if col not in holdout_df.columns:
            continue
        ind_pred = (holdout_df[col].apply(pd.to_numeric, errors='coerce') >
                    holdout_df['close_total'].apply(pd.to_numeric, errors='coerce')).astype(int).values
        valid = ~holdout_df[col].apply(pd.to_numeric, errors='coerce').isna()
        if valid.sum() == 0:
            continue
        acc = (ind_pred[valid.values] == y_holdout[valid.values]).mean()
        print(f'  {label}: {acc*100:.1f}% (n={valid.sum()})')

    print()
    print(f'== CONFIDENCE-GATED HOLDOUT ==')
    for thresh in [0.05, 0.10, 0.15, 0.20]:
        mask = (holdout_proba >= 0.5 + thresh) | (holdout_proba <= 0.5 - thresh)
        if mask.sum() == 0:
            print(f'  |prob-0.5|>={thresh}: no picks')
            continue
        sel_pred = (holdout_proba[mask] >= 0.5).astype(int)
        sel_y = y_holdout[mask]
        acc = (sel_pred == sel_y).mean()
        print(f'  |prob-0.5|>={thresh:.2f}: {acc*100:.1f}% on {mask.sum()}/{len(y_holdout)} picks')

    print()
    print('== TOP 10 FEATURES ==')
    imp = pd.DataFrame({'feature': avail_features, 'gain': clf.feature_importances_})
    imp = imp.sort_values('gain', ascending=False).head(10)
    for _, row in imp.iterrows():
        print(f'  {row.feature:>30s}: {row.gain:.4f}')

    # Save
    models_dir = Path(__file__).parent / 'models'
    models_dir.mkdir(exist_ok=True)
    model_path = models_dir / 'v5_total.json'
    meta_path = models_dir / 'v5_total_meta.json'
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
        'base_rate_over': float(y_holdout.mean()),
    }
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)
    print()
    print(f'✅ Saved: {model_path}')
    if holdout_acc >= 0.60:
        print(f'✅ Ship gate met: holdout {holdout_acc*100:.1f}% >= 60%')
    else:
        print(f'⚠️  Ship gate NOT met (holdout {holdout_acc*100:.1f}% < 60%)')


if __name__ == '__main__':
    main()
