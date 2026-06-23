"""Train the production v7 total model (Set A GB) and pickle it.

Set A_core: 11 features, 729 clean rows, GradientBoost.
Holdout 58.4%, conf-UNDER (p<=0.40) 64% on n=50.

Saves to models/total_v7_gb.pkl.

Run after backfills complete OR when you want to re-train on latest data.
"""
import os
import pickle
from datetime import date, timedelta
from pathlib import Path
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import accuracy_score

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

MODEL_PATH = Path(__file__).parent / 'models' / 'total_v7_gb.pkl'

BASE = [
    'home_sp_xera', 'away_sp_xera',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_bullpen_era', 'away_bullpen_era',
    'home_wrc_plus', 'away_wrc_plus',
    'park_run_factor', 'temperature',
    'projected_total', 'close_total',
    'home_score', 'away_score', 'game_date',
]

FEATS = [
    'sp_xera_min', 'sp_xera_max', 'sp_l3_min', 'sp_l3_max',
    'bp_avg', 'wrc_avg', 'park_run_factor', 'temperature',
    'gap_proj', 'day_of_week', 'line',
]


def engineer(df):
    for c in [col for col in df.columns if col != 'game_date']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['game_date'] = pd.to_datetime(df['game_date'])
    df['sp_xera_min'] = df[['home_sp_xera', 'away_sp_xera']].min(axis=1)
    df['sp_xera_max'] = df[['home_sp_xera', 'away_sp_xera']].max(axis=1)
    df['sp_l3_min'] = df[['home_pitcher_last_3_era', 'away_pitcher_last_3_era']].min(axis=1)
    df['sp_l3_max'] = df[['home_pitcher_last_3_era', 'away_pitcher_last_3_era']].max(axis=1)
    df['bp_avg'] = (df['home_bullpen_era'] + df['away_bullpen_era']) / 2
    df['wrc_avg'] = (df['home_wrc_plus'] + df['away_wrc_plus']) / 2
    df['gap_proj'] = df['projected_total'] - df['close_total']
    df['day_of_week'] = df['game_date'].dt.dayofweek
    df['line'] = df['close_total']
    return df


def main():
    print('Pulling 917+ graded games for production v7 training...')
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
    df['total'] = df['home_score'] + df['away_score']
    df['label'] = (df['total'] > df['close_total']).astype(int)
    df = df[df['total'] != df['close_total']].copy()
    df = engineer(df)

    df_clean = df[FEATS + ['label', 'game_date', 'total']].dropna()
    df_clean = df_clean.sort_values('game_date')
    print(f'Clean rows for training: {len(df_clean)}')

    # Walk-forward 14d holdout for final metrics
    cutoff = pd.Timestamp(date.today() - timedelta(days=14))
    train = df_clean[df_clean['game_date'] < cutoff]
    test = df_clean[df_clean['game_date'] >= cutoff]
    print(f'Train: {len(train)}  Holdout 14d: {len(test)}')

    # Train on ALL data for production (we'll holdout-evaluate first then refit)
    print()
    print('Evaluating on 14d holdout first...')
    gb_eval = GradientBoostingClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
    gb_eval.fit(train[FEATS], train['label'])
    pred = gb_eval.predict(test[FEATS])
    proba = gb_eval.predict_proba(test[FEATS])[:, 1]
    acc = accuracy_score(test['label'], pred) * 100
    m_cu = proba <= 0.40
    m_co = proba >= 0.60
    cu_correct = int((test['label'].values[m_cu] == 0).sum()) if m_cu.any() else 0
    co_correct = int((test['label'].values[m_co] == 1).sum()) if m_co.any() else 0
    print(f'Holdout acc: {acc:.1f}%')
    print(f'conf-UNDER (p<=0.40): {cu_correct}/{int(m_cu.sum())} ({100*cu_correct/max(1,int(m_cu.sum())):.0f}%)')
    print(f'conf-OVER  (p>=0.60): {co_correct}/{int(m_co.sum())} ({100*co_correct/max(1,int(m_co.sum())):.0f}%)')

    # Final production model: trained on ALL data
    print()
    print('Training production model on ALL clean data...')
    gb_prod = GradientBoostingClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
    gb_prod.fit(df_clean[FEATS], df_clean['label'])

    # Persist
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'model': gb_prod,
        'features': FEATS,
        'trained_on': len(df_clean),
        'trained_at': date.today().isoformat(),
        'holdout_acc': round(acc, 1),
        'conf_under_hit_rate': round(100 * cu_correct / max(1, int(m_cu.sum())), 1),
        'conf_under_n': int(m_cu.sum()),
        'conf_over_hit_rate': round(100 * co_correct / max(1, int(m_co.sum())), 1),
        'conf_over_n': int(m_co.sum()),
        'thresholds': {
            'conf_under': 0.40,  # Publish UNDER when p_over <= 0.40
            'conf_over': 0.60,   # NOT used in production yet (49-51% hit, no edge)
        },
        'notes': [
            'Set A_core only (11 features) — mastery dropped due to imputation noise',
            'Best signal is conf-UNDER (p<=0.40): 64% historical',
            'OVER side does NOT have edge — only publish UNDER picks from this model',
        ],
    }
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump(payload, f)
    print(f'Saved to: {MODEL_PATH}')
    print(f'Trained on: {len(df_clean)} games  Holdout: {acc:.1f}%  conf-UNDER: {100*cu_correct/max(1,int(m_cu.sum())):.0f}% (n={int(m_cu.sum())})')


if __name__ == '__main__':
    main()
