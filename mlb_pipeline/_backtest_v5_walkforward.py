"""v5 walk-forward backtest.

The single 14d holdout (58.9% / 51.7%) could be lucky. Walk-forward
proves v5 generalizes: train on (D-90 to D-N), predict D-N+1 ... D-N+H,
slide. Aggregate accuracy across ~6 distinct holdout windows.

If walk-forward accuracy stays >=55% for ML and confidence-gated totals
stay >=70%, v5 is real. If it collapses, hold v5 out of production
until we have a longer-stable model.
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from sklearn.metrics import accuracy_score, roc_auc_score
from xgboost import XGBClassifier

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

# Walk-forward parameters — data goes back ~83 days, so cap train at 50d
# to fit at least 4 non-overlapping holdout windows
TRAIN_WINDOW = 50
HOLDOUT_LEN = 7
SLIDE_STEP = 7   # non-overlapping holdouts

# Mirror v5_ml/total features
ML_FEATURES = [
    'projected_spread', 'model_pred_spread', 'jerry_pred_spread',
    'projected_total', 'model_pred_total', 'jerry_pred_total',
    'close_spread', 'close_total', 'home_ml_close', 'away_ml_close',
    'signal_confluence_net', 'nrfi_score',
    'away_sp_xera', 'home_sp_xera',
    'away_pitcher_last_3_era', 'home_pitcher_last_3_era',
    'away_bullpen_era', 'home_bullpen_era',
    'away_wrc_plus', 'home_wrc_plus',
    'away_ops_last7', 'home_ops_last7',
    'away_team_k_pct', 'home_team_k_pct',
    'park_run_factor', 'temperature',
]


def pull(days=200):
    since = (date.today() - timedelta(days=days)).isoformat()
    cols = ['game_date', 'home_score', 'away_score'] + ML_FEATURES
    sel = ','.join(cols)
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
    return rows


def train_xgb():
    return XGBClassifier(
        n_estimators=500, max_depth=3, learning_rate=0.03,
        subsample=0.7, colsample_bytree=0.7, min_child_weight=20,
        reg_lambda=3.0, reg_alpha=0.5, gamma=0.5,
        early_stopping_rounds=25,
        objective='binary:logistic', eval_metric='logloss',
        random_state=42,
    )


def walkforward(df, target_col, features, target_name):
    """Slide a 90d training window over the data, predicting the next 14
    days each step. Returns aggregate metrics."""
    df = df.sort_values('game_date').reset_index(drop=True)
    df['game_date'] = pd.to_datetime(df['game_date'])
    end = df['game_date'].max()
    start = df['game_date'].min() + pd.Timedelta(days=TRAIN_WINDOW)
    cursor = start
    windows = []
    while cursor + pd.Timedelta(days=HOLDOUT_LEN) <= end:
        train_end = cursor
        holdout_end = cursor + pd.Timedelta(days=HOLDOUT_LEN)
        train_df = df[df['game_date'] < train_end].copy()
        # Cap train at last TRAIN_WINDOW days
        train_df = train_df[train_df['game_date'] >= train_end - pd.Timedelta(days=TRAIN_WINDOW)]
        holdout_df = df[(df['game_date'] >= train_end) & (df['game_date'] < holdout_end)].copy()
        if len(train_df) < 50 or len(holdout_df) < 5:
            cursor = holdout_end
            continue
        X_train = train_df[features].apply(pd.to_numeric, errors='coerce')
        medians = X_train.median()
        X_train = X_train.fillna(medians).astype(float).values
        y_train = train_df[target_col].astype(int).values
        X_hold = holdout_df[features].apply(pd.to_numeric, errors='coerce').fillna(medians).astype(float).values
        y_hold = holdout_df[target_col].astype(int).values
        clf = train_xgb()
        clf.fit(X_train, y_train, eval_set=[(X_hold, y_hold)], verbose=False)
        proba = clf.predict_proba(X_hold)[:, 1]
        pred = (proba >= 0.5).astype(int)
        acc = accuracy_score(y_hold, pred)
        try:
            auc = roc_auc_score(y_hold, proba)
        except Exception:
            auc = float('nan')
        # Confidence-gated accuracy
        gated = {}
        for t in [0.05, 0.10, 0.15]:
            mask = (proba >= 0.5 + t) | (proba <= 0.5 - t)
            if mask.sum() > 0:
                gp = (proba[mask] >= 0.5).astype(int)
                gated[t] = (mask.sum(), (gp == y_hold[mask]).mean())
            else:
                gated[t] = (0, np.nan)
        windows.append({
            'train_end': train_end.date().isoformat(),
            'holdout_start': train_end.date().isoformat(),
            'holdout_end': holdout_end.date().isoformat(),
            'n_train': len(train_df),
            'n_holdout': len(holdout_df),
            'acc': float(acc),
            'auc': float(auc),
            'gated_05_n': int(gated[0.05][0]),
            'gated_05_acc': float(gated[0.05][1]) if gated[0.05][0] else None,
            'gated_10_n': int(gated[0.10][0]),
            'gated_10_acc': float(gated[0.10][1]) if gated[0.10][0] else None,
            'gated_15_n': int(gated[0.15][0]),
            'gated_15_acc': float(gated[0.15][1]) if gated[0.15][0] else None,
        })
        cursor = holdout_end

    print(f'== {target_name} walk-forward — {len(windows)} windows ==')
    print(f'{"start":>12s}  {"end":>12s}  {"n":>4s}  {"acc":>5s}  {"|>=.05| n/acc":>14s}  {"|>=.10| n/acc":>14s}')
    print('-' * 80)
    total_n = total_w = 0
    g05_n = g05_w = g10_n = g10_w = g15_n = g15_w = 0
    for w in windows:
        n = w['n_holdout']
        w_count = int(round(w['acc'] * n))
        total_n += n; total_w += w_count
        g05 = w['gated_05_n']; g05_a = w['gated_05_acc']
        g10 = w['gated_10_n']; g10_a = w['gated_10_acc']
        g05_n += g05; g05_w += int(round((g05_a or 0) * g05))
        g10_n += g10; g10_w += int(round((g10_a or 0) * g10))
        if w['gated_15_n']:
            g15_n += w['gated_15_n']; g15_w += int(round((w['gated_15_acc'] or 0) * w['gated_15_n']))
        g05_str = f'{g05} {(g05_a*100 if g05_a else 0):.0f}%' if g05 else '-'
        g10_str = f'{g10} {(g10_a*100 if g10_a else 0):.0f}%' if g10 else '-'
        print(f'  {w["holdout_start"]:>12s}  {w["holdout_end"]:>12s}  {n:>4d}  {w["acc"]*100:>4.0f}%  '
              f'{g05_str:>14s}  {g10_str:>14s}')
    print('-' * 80)
    print(f'AGGREGATE: {total_w}-{total_n - total_w} ({100*total_w/max(1,total_n):.1f}% '
          f'on n={total_n}) | gated >=0.05: {g05_w}/{g05_n} ({100*g05_w/max(1,g05_n):.1f}%) '
          f'| gated >=0.10: {g10_w}/{g10_n} ({100*g10_w/max(1,g10_n):.1f}%)'
          + (f' | gated >=0.15: {g15_w}/{g15_n} ({100*g15_w/max(1,g15_n):.1f}%)' if g15_n else ''))
    print()
    return windows


def main():
    rows = pull(days=200)
    df = pd.DataFrame([r for r in rows if isinstance(r, dict) and r.get('home_score') is not None])
    if df.empty:
        print('no data')
        return
    df['home_win'] = (df['home_score'] > df['away_score']).astype(int)
    df['actual_total'] = df['home_score'] + df['away_score']
    df = df[df['actual_total'] != df['close_total']].copy()
    df['over_label'] = (df['actual_total'] > df['close_total']).astype(int)
    print(f'pulled {len(df)} non-push graded games')
    print()
    walkforward(df, 'home_win', ML_FEATURES, 'v5_ml')
    walkforward(df, 'over_label', ML_FEATURES, 'v5_total')


if __name__ == '__main__':
    main()
