"""Train production v1 ML model (Set B, XGBoost) and pickle.

Set B_spread_fusion: 17 features, 208 clean rows.
XGBoost: 52.7% holdout, 64% conf-HOME hit on n=42.

Saves to models/ml_v1_xgb.pkl.
"""
import os
import pickle
from datetime import date, timedelta
from pathlib import Path
import requests
import pandas as pd
from dotenv import load_dotenv
from sklearn.metrics import accuracy_score

try:
    import xgboost as xgb
except ImportError:
    raise SystemExit('xgboost required: pip install xgboost')

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

MODEL_PATH = Path(__file__).parent / 'models' / 'ml_v1_xgb.pkl'

BASE = [
    'home_sp_xera', 'away_sp_xera', 'home_sp_k_pct', 'away_sp_k_pct',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_bullpen_era', 'away_bullpen_era',
    'home_wrc_plus', 'away_wrc_plus',
    'home_ops', 'away_ops',
    'home_team_k_pct', 'away_team_k_pct',
    'park_run_factor',
    'projected_spread', 'model_pred_spread', 'jerry_pred_spread',
    'close_spread', 'home_ml_close',
    'home_score', 'away_score', 'game_date',
]

FEATS = [
    'sp_xera_diff', 'sp_l3_diff', 'sp_k_pct_diff',
    'bp_diff', 'wrc_diff', 'ops_diff',
    'spread_proj', 'market_p_home', 'close_spread_signed',
    'park_run_factor', 'day_of_week',
    'spread_avg', 'spread_std', 'all3_home', 'all3_away',
    'spread_v4', 'spread_jerry',
]


def implied_p(ml):
    try: ml = float(ml)
    except (TypeError, ValueError): return None
    if ml < 0: return -ml / (-ml + 100)
    return 100 / (ml + 100)


def engineer(df):
    for c in [col for col in df.columns if col != 'game_date']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['game_date'] = pd.to_datetime(df['game_date'])
    df['sp_xera_diff'] = df['away_sp_xera'] - df['home_sp_xera']
    df['sp_l3_diff'] = df['away_pitcher_last_3_era'] - df['home_pitcher_last_3_era']
    df['sp_k_pct_diff'] = df['home_sp_k_pct'] - df['away_sp_k_pct']
    df['bp_diff'] = df['away_bullpen_era'] - df['home_bullpen_era']
    df['wrc_diff'] = df['home_wrc_plus'] - df['away_wrc_plus']
    df['ops_diff'] = df['home_ops'] - df['away_ops']
    df['day_of_week'] = df['game_date'].dt.dayofweek
    df['spread_proj'] = df['projected_spread']
    df['spread_v4'] = df['model_pred_spread']
    df['spread_jerry'] = df['jerry_pred_spread']
    df['spread_avg'] = df[['spread_proj', 'spread_v4', 'spread_jerry']].mean(axis=1)
    df['spread_std'] = df[['spread_proj', 'spread_v4', 'spread_jerry']].std(axis=1)
    df['all3_home'] = ((df['spread_proj'] > 0.3) & (df['spread_v4'] > 0.3) & (df['spread_jerry'] > 0.3)).astype(int)
    df['all3_away'] = ((df['spread_proj'] < -0.3) & (df['spread_v4'] < -0.3) & (df['spread_jerry'] < -0.3)).astype(int)
    df['market_p_home'] = df['home_ml_close'].apply(implied_p)
    df['close_spread_signed'] = df['close_spread']
    return df


def main():
    print('Pulling games for production ML training...')
    rows = []
    off = 0
    sel = ','.join(BASE)
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?home_score=not.is.null'
            f'&home_ml_close=not.is.null&select={sel}'
            f'&order=game_date.desc&limit=1000&offset={off}',
            headers=H, timeout=30,
        )
        chunk = r.json()
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000

    df = pd.DataFrame(rows)
    df['label'] = (df['home_score'] > df['away_score']).astype(int)
    df = df[df['home_score'] != df['away_score']].copy()
    df = engineer(df)

    df_clean = df[FEATS + ['label', 'game_date', 'home_ml_close']].dropna(subset=FEATS + ['label'])
    df_clean = df_clean.sort_values('game_date')
    print(f'Clean rows: {len(df_clean)}')

    cutoff = pd.Timestamp(date.today() - timedelta(days=14))
    train = df_clean[df_clean['game_date'] < cutoff]
    test = df_clean[df_clean['game_date'] >= cutoff]
    print(f'Train: {len(train)}  Holdout 14d: {len(test)}')

    print()
    print('Evaluating XGBoost on 14d holdout...')
    model_eval = xgb.XGBClassifier(n_estimators=500, max_depth=4, learning_rate=0.03, eval_metric='logloss', random_state=42)
    model_eval.fit(train[FEATS], train['label'])
    pred = model_eval.predict(test[FEATS])
    proba = model_eval.predict_proba(test[FEATS])[:, 1]
    acc = accuracy_score(test['label'], pred) * 100
    m_co = proba >= 0.60
    m_cu = proba <= 0.40
    co_c = int((test['label'].values[m_co] == 1).sum()) if m_co.any() else 0
    cu_c = int((test['label'].values[m_cu] == 0).sum()) if m_cu.any() else 0
    print(f'Holdout acc: {acc:.1f}%')
    print(f'conf-HOME (p>=0.60): {co_c}/{int(m_co.sum())} ({100*co_c/max(1,int(m_co.sum())):.0f}%)')
    print(f'conf-AWAY (p<=0.40): {cu_c}/{int(m_cu.sum())} ({100*cu_c/max(1,int(m_cu.sum())):.0f}%)')

    print()
    print('Training production model on ALL data...')
    model_prod = xgb.XGBClassifier(n_estimators=500, max_depth=4, learning_rate=0.03, eval_metric='logloss', random_state=42)
    model_prod.fit(df_clean[FEATS], df_clean['label'])

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'model': model_prod,
        'features': FEATS,
        'trained_on': len(df_clean),
        'trained_at': date.today().isoformat(),
        'holdout_acc': round(acc, 1),
        'conf_home_hit_rate': round(100 * co_c / max(1, int(m_co.sum())), 1),
        'conf_home_n': int(m_co.sum()),
        'conf_away_hit_rate': round(100 * cu_c / max(1, int(m_cu.sum())), 1),
        'conf_away_n': int(m_cu.sum()),
        'thresholds': {
            'conf_home': 0.60,  # publish HOME ML when p_home >= 0.60
            'conf_away': 0.40,  # NOT used (44% — no edge)
        },
        'notes': [
            'Set B_spread_fusion (17 features), XGBoost',
            'Conf-HOME is the only publishable signal (64% hist)',
            'Conf-AWAY at 44% has no edge — log only',
            'Lean range (0.40-0.60) also no edge',
        ],
    }
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump(payload, f)
    print(f'Saved to: {MODEL_PATH}')
    print(f'Trained on {len(df_clean)} games | Holdout {acc:.1f}% | conf-HOME {100*co_c/max(1,int(m_co.sum())):.0f}% (n={int(m_co.sum())})')


if __name__ == '__main__':
    main()
