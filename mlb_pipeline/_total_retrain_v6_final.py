"""Total retrain v6 — final attempt with ALL backfilled features.

Run AFTER:
  - backfill_pitcher_vs_team.py (mastery)
  - backfill_savant.py           (oaa, xwoba, barrel%, framing)
  - backfill_team_ops_history.py (ops_last7/14)
  - backfill_enrichment.py       (handedness splits, pitcher last-3)

Goal: holdout >= 65%, yesterday >= 70%.

Strategy:
  - Include all freshly backfilled features
  - Use 'has_X' flags so model knows real vs imputed
  - Median-impute remaining nulls
  - Walk-forward 14d holdout + yesterday-only out-of-sample
  - 5 models: LogReg, GB, GB-deep, RF, XGBoost
  - Composite ensemble
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

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

BASE = [
    'home_sp_xera', 'away_sp_xera', 'home_sp_k_pct', 'away_sp_k_pct',
    'home_sp_whiff_rate', 'away_sp_whiff_rate', 'home_sp_gb_pct', 'away_sp_gb_pct',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_pitcher_last_3_k_pct', 'away_pitcher_last_3_k_pct',
    'home_bullpen_era', 'away_bullpen_era',
    'home_wrc_plus', 'away_wrc_plus',
    'home_team_k_pct', 'away_team_k_pct',
    'park_run_factor', 'temperature', 'wind_mph',
    'projected_total', 'close_total',
    # Backfilled features:
    'home_pitcher_vs_team_era', 'away_pitcher_vs_team_era',
    'home_first_inning_era', 'away_first_inning_era',
    'home_team_oaa', 'away_team_oaa',
    'home_team_xwoba', 'away_team_xwoba',
    'home_team_barrel_pct', 'away_team_barrel_pct',
    'home_catcher_framing', 'away_catcher_framing',
    'home_ops_last7', 'away_ops_last7',
    'home_ops_last14', 'away_ops_last14',
    'home_wrc_vs_opp_hand', 'away_wrc_vs_opp_hand',
    'home_ops_vs_opp_hand', 'away_ops_vs_opp_hand',
    'home_score', 'away_score', 'game_date',
]


def main():
    print('=== Total retrain v6 — FINAL with all backfilled features ===')
    print('Pulling 917 graded games + backfilled features...')
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
    print(f'Total rows: {len(df)}  OVER rate: {df.label.mean()*100:.1f}%')
    print()
    print('Updated feature coverage:')
    cov_cols = ['home_pitcher_vs_team_era', 'home_first_inning_era',
                'home_team_oaa', 'home_team_xwoba', 'home_team_barrel_pct',
                'home_catcher_framing', 'home_ops_last7', 'home_ops_last14',
                'home_wrc_vs_opp_hand', 'home_ops_vs_opp_hand']
    for f in cov_cols:
        if f in df.columns:
            pct = df[f].notna().mean() * 100
            print(f'  {f:>32s}: {pct:>5.1f}%')
    print()

    # has-X flags BEFORE imputation
    for col in ['home_pitcher_vs_team_era', 'home_first_inning_era',
                'home_team_oaa', 'home_team_xwoba', 'home_catcher_framing',
                'home_ops_last7']:
        df[f'has_{col.replace("home_", "")}'] = df[col].notna().astype(int)

    # Median-impute every backfilled column
    impute_cols = [c for c in BASE if c in df.columns and c not in
                   ['close_total', 'home_score', 'away_score', 'game_date',
                    'park_run_factor', 'temperature', 'projected_total']]
    for col in impute_cols:
        med = df[col].median()
        df[col] = df[col].fillna(med)

    # Derived
    df['sp_xera_min'] = df[['home_sp_xera', 'away_sp_xera']].min(axis=1)
    df['sp_xera_max'] = df[['home_sp_xera', 'away_sp_xera']].max(axis=1)
    df['sp_l3_min'] = df[['home_pitcher_last_3_era', 'away_pitcher_last_3_era']].min(axis=1)
    df['sp_l3_max'] = df[['home_pitcher_last_3_era', 'away_pitcher_last_3_era']].max(axis=1)
    df['sp_k_pct_avg'] = (df['home_sp_k_pct'] + df['away_sp_k_pct']) / 2
    df['sp_whiff_avg'] = (df['home_sp_whiff_rate'] + df['away_sp_whiff_rate']) / 2
    df['sp_gb_avg'] = (df['home_sp_gb_pct'] + df['away_sp_gb_pct']) / 2
    df['bp_avg'] = (df['home_bullpen_era'] + df['away_bullpen_era']) / 2
    df['wrc_avg'] = (df['home_wrc_plus'] + df['away_wrc_plus']) / 2
    df['gap_proj'] = df['projected_total'] - df['close_total']
    df['day_of_week'] = df['game_date'].dt.dayofweek
    df['is_sunday'] = (df['day_of_week'] == 6).astype(int)
    df['month'] = df['game_date'].dt.month

    df['pvt_era_min'] = df[['home_pitcher_vs_team_era', 'away_pitcher_vs_team_era']].min(axis=1)
    df['pvt_era_max'] = df[['home_pitcher_vs_team_era', 'away_pitcher_vs_team_era']].max(axis=1)
    df['pvt_era_avg'] = (df['home_pitcher_vs_team_era'] + df['away_pitcher_vs_team_era']) / 2
    df['pvt_mastery_either'] = ((df['home_pitcher_vs_team_era'] < 3.0) | (df['away_pitcher_vs_team_era'] < 3.0)).astype(int)
    df['pvt_mastery_both'] = ((df['home_pitcher_vs_team_era'] < 3.5) & (df['away_pitcher_vs_team_era'] < 3.5)).astype(int)
    df['pvt_trouble'] = ((df['home_pitcher_vs_team_era'] > 6.0) | (df['away_pitcher_vs_team_era'] > 6.0)).astype(int)

    df['fi_era_min'] = df[['home_first_inning_era', 'away_first_inning_era']].min(axis=1)
    df['fi_era_max'] = df[['home_first_inning_era', 'away_first_inning_era']].max(axis=1)
    df['fi_era_avg'] = (df['home_first_inning_era'] + df['away_first_inning_era']) / 2

    df['oaa_avg'] = (df['home_team_oaa'] + df['away_team_oaa']) / 2
    df['xwoba_avg'] = (df['home_team_xwoba'] + df['away_team_xwoba']) / 2
    df['barrel_avg'] = (df['home_team_barrel_pct'] + df['away_team_barrel_pct']) / 2
    df['framing_avg'] = (df['home_catcher_framing'] + df['away_catcher_framing']) / 2

    df['ops7_avg'] = (df['home_ops_last7'] + df['away_ops_last7']) / 2
    df['ops7_max'] = df[['home_ops_last7', 'away_ops_last7']].max(axis=1)
    df['ops14_avg'] = (df['home_ops_last14'] + df['away_ops_last14']) / 2
    df['ops_form_delta_home'] = df['home_ops_last7'] - df['home_ops_last14']
    df['ops_form_delta_away'] = df['away_ops_last7'] - df['away_ops_last14']
    df['ops_vs_opp_hand_avg'] = (df['home_ops_vs_opp_hand'] + df['away_ops_vs_opp_hand']) / 2
    df['wrc_vs_opp_hand_avg'] = (df['home_wrc_vs_opp_hand'] + df['away_wrc_vs_opp_hand']) / 2

    df['line'] = df['close_total']

    feats = [
        # SP
        'sp_xera_min', 'sp_xera_max', 'sp_l3_min', 'sp_l3_max', 'sp_k_pct_avg',
        'sp_whiff_avg', 'sp_gb_avg',
        # BP / offense
        'bp_avg', 'wrc_avg', 'park_run_factor', 'temperature', 'wind_mph',
        # Calendar
        'gap_proj', 'day_of_week', 'is_sunday', 'month', 'line',
        # Mastery
        'pvt_era_min', 'pvt_era_max', 'pvt_era_avg',
        'pvt_mastery_either', 'pvt_mastery_both', 'pvt_trouble',
        # First inning
        'fi_era_min', 'fi_era_max', 'fi_era_avg',
        # Savant defense / offense quality
        'oaa_avg', 'xwoba_avg', 'barrel_avg', 'framing_avg',
        # Recent form
        'ops7_avg', 'ops7_max', 'ops14_avg',
        'ops_form_delta_home', 'ops_form_delta_away',
        'ops_vs_opp_hand_avg', 'wrc_vs_opp_hand_avg',
        # Has-X flags
        'has_pitcher_vs_team_era', 'has_first_inning_era',
        'has_team_oaa', 'has_team_xwoba', 'has_catcher_framing', 'has_ops_last7',
    ]
    feats = [f for f in feats if f in df.columns]

    extra = [c for c in ['label', 'game_date', 'total', 'line'] if c not in feats]
    df_s = df[feats + extra].dropna(subset=feats + ['label'])
    df_s = df_s.loc[:, ~df_s.columns.duplicated()].sort_values('game_date')
    print(f'Clean rows after imputation: {len(df_s)} (target: ~713)')

    yest_date = pd.Timestamp('2026-06-22')
    cutoff = pd.Timestamp(date.today() - timedelta(days=14))
    train = df_s[df_s['game_date'] < cutoff]
    test = df_s[df_s['game_date'] >= cutoff]
    yest = df_s[df_s['game_date'] == yest_date]
    train_strict = df_s[df_s['game_date'] < yest_date]
    print(f'Train: {len(train)}  Holdout 14d: {len(test)}  Yesterday: {len(yest)}')
    print()

    X_tr, y_tr = train[feats], train['label']
    X_te, y_te = test[feats], test['label']

    models = [
        ('LogReg', LogisticRegression(max_iter=2000, C=1.0)),
        ('LogReg-C0.3', LogisticRegression(max_iter=2000, C=0.3)),
        ('LogReg-C3', LogisticRegression(max_iter=2000, C=3.0)),
        ('GradientBoost', GradientBoostingClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)),
        ('GradientBoost-deep', GradientBoostingClassifier(n_estimators=500, max_depth=5, learning_rate=0.03, random_state=42)),
        ('RandomForest', RandomForestClassifier(n_estimators=500, max_depth=8, random_state=42, min_samples_leaf=8)),
    ]
    if HAS_XGB:
        models.append(('XGBoost', xgb.XGBClassifier(n_estimators=500, max_depth=4, learning_rate=0.03, eval_metric='logloss', random_state=42)))

    print(f'{"Model":>22s} | {"holdout":>8s} | {"yest":>10s} | conf-UNDER (p<=.40) | conf-OVER (p>=.60)')
    print('-' * 110)
    for name, model in models:
        try:
            if name.startswith('LogReg'):
                sc = StandardScaler(); xs = sc.fit_transform(X_tr); xst = sc.transform(X_te)
                model.fit(xs, y_tr); pred = model.predict(xst); proba = model.predict_proba(xst)[:, 1]
            else:
                model.fit(X_tr, y_tr); pred = model.predict(X_te); proba = model.predict_proba(X_te)[:, 1]
        except Exception as e:
            print(f'  {name:>20s} | ERROR: {str(e)[:60]}')
            continue
        acc = accuracy_score(y_te, pred) * 100
        m_cu = proba <= 0.40; m_co = proba >= 0.60
        cu_c = int((y_te.values[m_cu] == 0).sum()) if m_cu.any() else 0
        co_c = int((y_te.values[m_co] == 1).sum()) if m_co.any() else 0
        cu_str = f'{cu_c}/{int(m_cu.sum())} ({100*cu_c/max(1,int(m_cu.sum())):.0f}%)'
        co_str = f'{co_c}/{int(m_co.sum())} ({100*co_c/max(1,int(m_co.sum())):.0f}%)'
        ystr = '-'
        if len(yest):
            try:
                if name.startswith('LogReg'):
                    sc2 = StandardScaler(); xs2 = sc2.fit_transform(train_strict[feats]); xy = sc2.transform(yest[feats])
                    m2 = LogisticRegression(max_iter=2000, C=model.C); m2.fit(xs2, train_strict['label']); yp = m2.predict(xy)
                else:
                    m2 = model.__class__(**model.get_params()); m2.fit(train_strict[feats], train_strict['label']); yp = m2.predict(yest[feats])
                yc = int((yp == yest['label'].values).sum())
                ystr = f'{yc}/{len(yest)} ({100*yc/len(yest):.0f}%)'
            except Exception as e:
                ystr = 'ERR'
        print(f'  {name:>20s} | {acc:>6.1f}% | {ystr:>10s} | {cu_str:>20s} | {co_str:>20s}')

    # Top features
    print()
    gb = GradientBoostingClassifier(n_estimators=500, max_depth=5, learning_rate=0.03, random_state=42)
    gb.fit(X_tr, y_tr)
    imp = sorted(zip(feats, gb.feature_importances_), key=lambda x: -x[1])
    print('Top 20 feature importance (GB-deep):')
    for f, i in imp[:20]:
        print(f'  {f:>32s}: {i*100:>5.1f}%')


if __name__ == '__main__':
    main()
