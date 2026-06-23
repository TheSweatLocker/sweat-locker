"""Total retrain v5 — median-impute the mastery features instead of dropna.

The mastery features (pvt_era) are real predictors but only on 65% of rows.
Dropping incomplete rows wastes data. Median-impute to keep all 713 rows.
Add 'has_pvt_data' indicator so model knows if mastery is real vs imputed.
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
from sklearn.impute import SimpleImputer

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
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_pitcher_last_3_k_pct', 'away_pitcher_last_3_k_pct',
    'home_bullpen_era', 'away_bullpen_era',
    'home_wrc_plus', 'away_wrc_plus',
    'park_run_factor', 'temperature',
    'projected_total', 'close_total',
    'home_pitcher_vs_team_era', 'away_pitcher_vs_team_era',
    'home_first_inning_era', 'away_first_inning_era',
    'home_score', 'away_score', 'game_date',
]


def main():
    print('Pulling 917 graded games + median-imputing mastery features...')
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

    # Has-data flags
    df['has_pvt_home'] = df['home_pitcher_vs_team_era'].notna().astype(int)
    df['has_pvt_away'] = df['away_pitcher_vs_team_era'].notna().astype(int)
    df['has_fi_home'] = df['home_first_inning_era'].notna().astype(int)
    df['has_fi_away'] = df['away_first_inning_era'].notna().astype(int)

    # Median-impute mastery & first_inning
    for col in ['home_pitcher_vs_team_era', 'away_pitcher_vs_team_era',
                'home_first_inning_era', 'away_first_inning_era']:
        med = df[col].median()
        df[col] = df[col].fillna(med)

    # Derived
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

    df['pvt_era_min'] = df[['home_pitcher_vs_team_era', 'away_pitcher_vs_team_era']].min(axis=1)
    df['pvt_era_max'] = df[['home_pitcher_vs_team_era', 'away_pitcher_vs_team_era']].max(axis=1)
    df['pvt_era_avg'] = (df['home_pitcher_vs_team_era'] + df['away_pitcher_vs_team_era']) / 2
    df['pvt_mastery_either'] = ((df['home_pitcher_vs_team_era'] < 3.0) | (df['away_pitcher_vs_team_era'] < 3.0)).astype(int)
    df['pvt_mastery_both'] = ((df['home_pitcher_vs_team_era'] < 3.5) & (df['away_pitcher_vs_team_era'] < 3.5)).astype(int)
    df['pvt_trouble'] = ((df['home_pitcher_vs_team_era'] > 6.0) | (df['away_pitcher_vs_team_era'] > 6.0)).astype(int)
    df['fi_era_min'] = df[['home_first_inning_era', 'away_first_inning_era']].min(axis=1)
    df['fi_era_max'] = df[['home_first_inning_era', 'away_first_inning_era']].max(axis=1)
    df['fi_era_avg'] = (df['home_first_inning_era'] + df['away_first_inning_era']) / 2

    df['line'] = df['close_total']

    feats = [
        'sp_xera_min', 'sp_xera_max', 'sp_l3_min', 'sp_l3_max', 'sp_k_pct_avg',
        'bp_avg', 'wrc_avg', 'park_run_factor', 'temperature',
        'gap_proj', 'day_of_week', 'month', 'line',
        'pvt_era_min', 'pvt_era_max', 'pvt_era_avg',
        'pvt_mastery_either', 'pvt_mastery_both', 'pvt_trouble',
        'fi_era_min', 'fi_era_max', 'fi_era_avg',
        'has_pvt_home', 'has_pvt_away',
    ]

    extra = [c for c in ['label', 'game_date', 'total', 'line'] if c not in feats]
    df_s = df[feats + extra].dropna(subset=feats + ['label'])
    df_s = df_s.loc[:, ~df_s.columns.duplicated()].sort_values('game_date')
    print(f'Clean rows: {len(df_s)} (vs 337 without imputation — preserved {len(df_s)-337} more rows)')

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
    best = (None, 0)
    for name, model in models:
        try:
            if name.startswith('LogReg'):
                sc = StandardScaler(); xs = sc.fit_transform(X_tr); xst = sc.transform(X_te)
                model.fit(xs, y_tr); pred = model.predict(xst); proba = model.predict_proba(xst)[:, 1]
            else:
                model.fit(X_tr, y_tr); pred = model.predict(X_te); proba = model.predict_proba(X_te)[:, 1]
        except Exception as e:
            print(f'  {name:>20s} | ERROR: {str(e)[:50]}')
            continue
        acc = accuracy_score(y_te, pred) * 100
        m_cu = proba <= 0.40; m_co = proba >= 0.60
        cu_c = int((y_te.values[m_cu] == 0).sum()) if m_cu.any() else 0
        co_c = int((y_te.values[m_co] == 1).sum()) if m_co.any() else 0
        cu_str = f'{cu_c}/{int(m_cu.sum())} ({100*cu_c/max(1,int(m_cu.sum())):.0f}%)'
        co_str = f'{co_c}/{int(m_co.sum())} ({100*co_c/max(1,int(m_co.sum())):.0f}%)'
        ystr = '-'
        ypct = 0
        if len(yest):
            try:
                if name.startswith('LogReg'):
                    sc2 = StandardScaler(); xs2 = sc2.fit_transform(train_strict[feats]); xy = sc2.transform(yest[feats])
                    m2 = LogisticRegression(max_iter=2000, C=model.C); m2.fit(xs2, train_strict['label']); yp = m2.predict(xy)
                else:
                    m2 = model.__class__(**model.get_params()); m2.fit(train_strict[feats], train_strict['label']); yp = m2.predict(yest[feats])
                yc = int((yp == yest['label'].values).sum())
                ypct = 100*yc/len(yest)
                ystr = f'{yc}/{len(yest)} ({ypct:.0f}%)'
            except Exception as e:
                ystr = 'ERR'
        # composite score: 60% holdout + 40% yest
        score = 0.6 * acc + 0.4 * ypct
        if score > best[1]: best = (name, score)
        print(f'  {name:>20s} | {acc:>6.1f}% | {ystr:>10s} | {cu_str:>20s} | {co_str:>20s}')

    print()
    print(f'BEST MODEL by 60/40 composite: {best[0]} (score {best[1]:.1f})')

    # Confidence threshold sweep on best model
    print()
    print('=== CONFIDENCE SWEEP on LogReg (holdout) ===')
    sc = StandardScaler(); xs = sc.fit_transform(X_tr); xst = sc.transform(X_te)
    lr = LogisticRegression(max_iter=2000, C=1.0).fit(xs, y_tr)
    proba = lr.predict_proba(xst)[:, 1]
    y = y_te.values
    print(f'{"Threshold (UNDER)":>20s} | {"n picks":>8s} | {"hits":>20s}')
    for thr in [0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15]:
        mask = proba <= thr
        n = int(mask.sum())
        wins = int((y[mask] == 0).sum()) if n else 0
        rate = f'{100*wins/n:.0f}%' if n else '-'
        print(f'  p <= {thr:.2f}            | {n:>8d} | {wins}/{n} ({rate})')

    # Per-game yesterday detail (best model)
    print()
    print('=== PER-GAME YESTERDAY (LogReg+mastery) ===')
    if len(yest):
        sc2 = StandardScaler(); xs2 = sc2.fit_transform(train_strict[feats]); xy = sc2.transform(yest[feats])
        m2 = LogisticRegression(max_iter=2000, C=1.0).fit(xs2, train_strict['label'])
        yp = m2.predict(xy)
        yproba = m2.predict_proba(xy)[:, 1]
        for (idx, row), p, prob in zip(yest.iterrows(), yp, yproba):
            actual = 'OVER' if row['label'] == 1 else 'UNDER'
            pred = 'OVER' if p == 1 else 'UNDER'
            mark = 'OK ' if actual == pred else 'X  '
            print(f'  {mark} line={row["line"]:.1f} total={int(row["total"])} actual={actual:5s} pred={pred:5s} p_over={prob:.2f}')


if __name__ == '__main__':
    main()
