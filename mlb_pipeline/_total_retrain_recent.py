"""Total retrain on RECENT-only data (last 60d, 90d, 180d).

Hypothesis: training on older data may include outdated patterns.
Test: train on 60d / 90d / 180d / all, compare yesterday + holdout.
Also tries:
  - per-tier production thresholds (where does conf-UNDER stop being predictive?)
  - per-season-segment models
"""
import os
from datetime import date, timedelta
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

BASE = [
    'home_sp_xera', 'away_sp_xera', 'home_sp_k_pct', 'away_sp_k_pct',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_bullpen_era', 'away_bullpen_era',
    'home_wrc_plus', 'away_wrc_plus',
    'park_run_factor', 'temperature',
    'projected_total', 'close_total', 'home_score', 'away_score', 'game_date',
]


def main():
    print('Pulling 917 graded games...')
    rows = []
    off = 0
    sel = ','.join(BASE)
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?home_score=not.is.null'
            f'&close_total=not.is.null&select={sel}'
            f'&order=game_date.desc&limit=1000&offset={off}',
            headers=H, timeout=30,
        )
        chunk = r.json()
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000

    df = pd.DataFrame(rows)
    for c in [col for col in df.columns if col != 'game_date']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['game_date'] = pd.to_datetime(df['game_date'])
    df['total'] = df['home_score'] + df['away_score']
    df['label'] = (df['total'] > df['close_total']).astype(int)
    df = df[df['total'] != df['close_total']].copy()

    df['sp_xera_min'] = df[['home_sp_xera', 'away_sp_xera']].min(axis=1)
    df['sp_xera_max'] = df[['home_sp_xera', 'away_sp_xera']].max(axis=1)
    df['sp_l3_min'] = df[['home_pitcher_last_3_era', 'away_pitcher_last_3_era']].min(axis=1)
    df['sp_l3_max'] = df[['home_pitcher_last_3_era', 'away_pitcher_last_3_era']].max(axis=1)
    df['sp_k_pct_avg'] = (df['home_sp_k_pct'] + df['away_sp_k_pct']) / 2
    df['bp_avg'] = (df['home_bullpen_era'] + df['away_bullpen_era']) / 2
    df['wrc_avg'] = (df['home_wrc_plus'] + df['away_wrc_plus']) / 2
    df['gap_proj'] = df['projected_total'] - df['close_total']
    df['day_of_week'] = df['game_date'].dt.dayofweek
    df['month'] = df['game_date'].dt.month
    df['line'] = df['close_total']

    feats = ['sp_xera_min', 'sp_xera_max', 'sp_l3_min', 'sp_l3_max',
             'sp_k_pct_avg', 'bp_avg', 'wrc_avg',
             'park_run_factor', 'temperature',
             'gap_proj', 'day_of_week', 'month', 'line']

    df_s = df[feats + ['label', 'game_date', 'total']].dropna().sort_values('game_date')
    print(f'Complete rows: {len(df_s)}  OVER rate: {df_s.label.mean()*100:.1f}%')

    yest_date = pd.Timestamp('2026-06-22')
    yest = df_s[df_s['game_date'] == yest_date]

    print()
    print('=== EXPERIMENT 1: training window size ===')
    print(f'{"Train window":>18s} | {"Train n":>8s} | {"Hold14 acc":>11s} | {"Yest acc":>10s} | {"conf-UNDER":>15s}')
    print('-' * 80)
    for window_days in [30, 60, 90, 180, 365, 999]:
        cutoff_recent = pd.Timestamp(date.today() - timedelta(days=window_days))
        holdout_cutoff = pd.Timestamp(date.today() - timedelta(days=14))
        train = df_s[(df_s['game_date'] >= cutoff_recent) & (df_s['game_date'] < holdout_cutoff)]
        test = df_s[df_s['game_date'] >= holdout_cutoff]
        train_for_yest = df_s[(df_s['game_date'] >= cutoff_recent) & (df_s['game_date'] < yest_date)]
        if len(train) < 30: continue

        gb = GradientBoostingClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
        gb.fit(train[feats], train['label'])
        pred = gb.predict(test[feats])
        proba = gb.predict_proba(test[feats])[:, 1]
        acc = accuracy_score(test['label'], pred) * 100
        m_cu = proba <= 0.40
        cu_c = int((test['label'].values[m_cu] == 0).sum()) if m_cu.any() else 0
        cu_str = f'{cu_c}/{int(m_cu.sum())} ({100*cu_c/max(1,int(m_cu.sum())):.0f}%)'

        yest_str = '-'
        if len(yest) > 0 and len(train_for_yest) >= 30:
            gb2 = GradientBoostingClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
            gb2.fit(train_for_yest[feats], train_for_yest['label'])
            yp = gb2.predict(yest[feats])
            yc = int((yp == yest['label'].values).sum())
            yest_str = f'{yc}/{len(yest)} ({100*yc/len(yest):.0f}%)'

        print(f'  last {window_days:>3}d        | {len(train):>8d} | {acc:>9.1f}% | {yest_str:>10s} | {cu_str:>15s}')

    print()
    print('=== EXPERIMENT 2: confidence threshold sweep (last 30d test) ===')
    print('  How does conf-UNDER hit rate change as threshold tightens?')
    cutoff_recent = pd.Timestamp(date.today() - timedelta(days=120))
    holdout_cutoff = pd.Timestamp(date.today() - timedelta(days=30))
    train = df_s[(df_s['game_date'] >= cutoff_recent) & (df_s['game_date'] < holdout_cutoff)]
    test = df_s[df_s['game_date'] >= holdout_cutoff]
    if len(train) >= 30 and len(test) > 0:
        gb = GradientBoostingClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
        gb.fit(train[feats], train['label'])
        proba = gb.predict_proba(test[feats])[:, 1]
        y = test['label'].values
        print(f'{"Threshold":>12s} | {"n picks":>8s} | {"UNDER hits":>12s}')
        for thr in [0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20]:
            mask = proba <= thr
            n = int(mask.sum())
            wins = int((y[mask] == 0).sum()) if n else 0
            rate = f'{100*wins/n:.0f}%' if n else '-'
            print(f'    p <= {thr:.2f}  | {n:>8d} | {wins}/{n} ({rate})')

    print()
    print('=== EXPERIMENT 3: per-month seasonality check ===')
    df_s['month_name'] = df_s['game_date'].dt.strftime('%Y-%m')
    for month in sorted(df_s['month_name'].unique()):
        sub = df_s[df_s['month_name'] == month]
        rate = sub['label'].mean() * 100
        n = len(sub)
        if n >= 30:
            print(f'  {month}: OVER {sub["label"].sum()}/{n} ({rate:.0f}%)  avg total {sub["total"].mean():.2f}')


if __name__ == '__main__':
    main()
