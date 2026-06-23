"""Total model retrain v2 — augmented features from existing data.

Adds engineered features that don't require new data fetches:
  - day_of_week, is_weekend, month (calendar features)
  - sp_form_delta (xera vs L3 ERA — recent form trend)
  - sp_xera_gap (mismatched pitchers)
  - sp_k_pct_gap (k-rate disparity)
  - bp_gap, wrc_gap
  - composite stats (avg, std/disagreement, line gap)
  - model agreement flags (all3_over, all3_under)
  - park × temp interaction
  - coors flag, dome flag (already exists)

Goal: push 64% retrain ceiling toward 75%+ via better features.
Walk-forward holdout = last 14d; also tests yesterday (2026-06-22) specifically.
"""
import os
from datetime import date, timedelta
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

BASE_COLS = [
    'home_sp_xera', 'away_sp_xera', 'home_sp_era', 'away_sp_era',
    'home_sp_k_pct', 'away_sp_k_pct', 'home_sp_whiff_rate', 'away_sp_whiff_rate',
    'home_sp_gb_pct', 'away_sp_gb_pct',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_pitcher_last_3_k_pct', 'away_pitcher_last_3_k_pct',
    'home_bullpen_era', 'away_bullpen_era',
    'home_runs_per_game', 'away_runs_per_game',
    'home_wrc_plus', 'away_wrc_plus',
    'home_ops', 'away_ops',
    'park_run_factor', 'temperature', 'wind_mph',
    'home_team_k_pct', 'away_team_k_pct',
    'home_k_gap', 'away_k_gap',
    'nrfi_score', 'signal_confluence_net',
    'home_sp_days_rest', 'away_sp_days_rest',
    'dome_game_flag',
    'projected_total', 'model_pred_total', 'jerry_pred_total',
    'close_total', 'open_total', 'home_score', 'away_score', 'game_date',
]


def pull_all():
    rows = []
    offset = 0
    sel = ','.join(BASE_COLS)
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?home_score=not.is.null'
            f'&close_total=not.is.null&select={sel}'
            f'&order=game_date.desc&limit=1000&offset={offset}',
            headers=H, timeout=30,
        )
        chunk = r.json()
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def engineer(df):
    """Add derived features from base columns."""
    # Coerce all numeric
    numeric_cols = [c for c in df.columns if c not in ('game_date',)]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce')

    df['game_date'] = pd.to_datetime(df['game_date'])

    # Calendar features
    df['day_of_week'] = df['game_date'].dt.dayofweek  # 0=Mon, 6=Sun
    df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)
    df['is_sunday'] = (df['day_of_week'] == 6).astype(int)
    df['month'] = df['game_date'].dt.month

    # SP form deltas (positive = recent form worse than season xERA)
    df['sp_form_delta_home'] = df['home_pitcher_last_3_era'] - df['home_sp_xera']
    df['sp_form_delta_away'] = df['away_pitcher_last_3_era'] - df['away_sp_xera']
    df['sp_form_delta_max'] = df[['sp_form_delta_home', 'sp_form_delta_away']].max(axis=1)
    df['sp_form_delta_min'] = df[['sp_form_delta_home', 'sp_form_delta_away']].min(axis=1)

    # SP aggregates
    df['sp_xera_avg'] = (df['home_sp_xera'] + df['away_sp_xera']) / 2
    df['sp_xera_min'] = df[['home_sp_xera', 'away_sp_xera']].min(axis=1)
    df['sp_xera_max'] = df[['home_sp_xera', 'away_sp_xera']].max(axis=1)
    df['sp_xera_gap'] = (df['home_sp_xera'] - df['away_sp_xera']).abs()
    df['sp_l3_avg'] = (df['home_pitcher_last_3_era'] + df['away_pitcher_last_3_era']) / 2
    df['sp_l3_min'] = df[['home_pitcher_last_3_era', 'away_pitcher_last_3_era']].min(axis=1)
    df['sp_l3_max'] = df[['home_pitcher_last_3_era', 'away_pitcher_last_3_era']].max(axis=1)
    df['sp_k_pct_avg'] = (df['home_sp_k_pct'] + df['away_sp_k_pct']) / 2
    df['sp_k_pct_max'] = df[['home_sp_k_pct', 'away_sp_k_pct']].max(axis=1)
    df['sp_whiff_avg'] = (df['home_sp_whiff_rate'] + df['away_sp_whiff_rate']) / 2
    df['sp_gb_avg'] = (df['home_sp_gb_pct'] + df['away_sp_gb_pct']) / 2

    # BP aggregates
    df['bp_avg'] = (df['home_bullpen_era'] + df['away_bullpen_era']) / 2
    df['bp_min'] = df[['home_bullpen_era', 'away_bullpen_era']].min(axis=1)
    df['bp_max'] = df[['home_bullpen_era', 'away_bullpen_era']].max(axis=1)
    df['bp_gap'] = (df['home_bullpen_era'] - df['away_bullpen_era']).abs()

    # Offense
    df['wrc_avg'] = (df['home_wrc_plus'] + df['away_wrc_plus']) / 2
    df['wrc_max'] = df[['home_wrc_plus', 'away_wrc_plus']].max(axis=1)
    df['wrc_gap'] = (df['home_wrc_plus'] - df['away_wrc_plus']).abs()
    df['rpg_avg'] = (df['home_runs_per_game'] + df['away_runs_per_game']) / 2
    df['rpg_sum'] = df['home_runs_per_game'] + df['away_runs_per_game']
    df['team_k_avg'] = (df['home_team_k_pct'] + df['away_team_k_pct']) / 2

    # Composite model gaps
    df['composite_avg'] = df[['projected_total', 'model_pred_total', 'jerry_pred_total']].mean(axis=1)
    df['composite_std'] = df[['projected_total', 'model_pred_total', 'jerry_pred_total']].std(axis=1)
    df['gap_proj'] = df['projected_total'] - df['close_total']
    df['gap_v4'] = df['model_pred_total'] - df['close_total']
    df['gap_jerry'] = df['jerry_pred_total'] - df['close_total']
    df['gap_composite'] = df['composite_avg'] - df['close_total']

    # Model agreement flags
    df['all3_over'] = ((df['gap_proj'] > 0.3) & (df['gap_v4'] > 0.3) & (df['gap_jerry'] > 0.3)).astype(int)
    df['all3_under'] = ((df['gap_proj'] < -0.3) & (df['gap_v4'] < -0.3) & (df['gap_jerry'] < -0.3)).astype(int)

    # Line movement
    df['line_movement'] = df['close_total'] - df['open_total']

    # Park interactions
    df['coors_flag'] = (df['park_run_factor'] > 110).astype(int)
    df['hitter_park'] = (df['park_run_factor'] > 103).astype(int)
    df['pitcher_park'] = (df['park_run_factor'] < 97).astype(int)
    df['park_temp_interaction'] = df['park_run_factor'] * (df['temperature'] / 70.0)
    df['cold_game'] = (df['temperature'] < 60).astype(int)
    df['hot_game'] = (df['temperature'] >= 85).astype(int)

    # Rest
    df['rest_max'] = df[['home_sp_days_rest', 'away_sp_days_rest']].max(axis=1)

    return df


def main():
    print('Pulling 917 graded games + engineering 40+ derived features...')
    rows = pull_all()
    df = pd.DataFrame(rows)
    df['total'] = df['home_score'] + df['away_score']
    df['line'] = df['close_total']
    df['label'] = (df['total'] > df['line']).astype(int)
    df = df[df['total'] != df['line']].copy()
    df = engineer(df)
    print(f'Total rows: {len(df)}')
    print(f'OVER rate: {df.label.mean()*100:.1f}%')

    # Feature sets to test
    set_minimal = [
        'sp_xera_min', 'sp_xera_max', 'bp_avg', 'wrc_avg',
        'park_run_factor', 'line',
    ]
    set_solid = set_minimal + [
        'sp_l3_min', 'sp_l3_max', 'sp_k_pct_avg',
        'temperature', 'gap_proj', 'composite_avg',
        'day_of_week', 'is_sunday', 'month',
    ]
    set_full = set_solid + [
        'sp_form_delta_max', 'sp_form_delta_min', 'sp_xera_gap',
        'sp_whiff_avg', 'sp_gb_avg', 'bp_gap', 'wrc_gap', 'wrc_max',
        'team_k_avg', 'rpg_sum',
        'composite_std', 'gap_v4', 'gap_jerry', 'gap_composite',
        'all3_over', 'all3_under',
        'line_movement', 'coors_flag', 'park_temp_interaction',
        'cold_game', 'hot_game', 'rest_max', 'nrfi_score',
    ]

    print()
    print('Feature coverage on full set:')
    for f in set_full:
        if f in df.columns:
            pct = df[f].notna().mean() * 100
            print(f'  {f:>26s}: {pct:>5.1f}%')

    yest_date = pd.Timestamp('2026-06-22')

    for set_name, feats in [
        ('minimal (6)', set_minimal),
        ('solid (15)', set_solid),
        ('full (38)', set_full),
    ]:
        print()
        print('=' * 90)
        print(f'FEATURE SET: {set_name}')
        print('=' * 90)
        df_s = df[feats + ['label', 'game_date', 'total', 'line']].dropna()
        print(f'Complete rows: {len(df_s)}')
        if len(df_s) < 100:
            print('Too few rows, skipping')
            continue

        df_s = df_s.sort_values('game_date')

        # Walk-forward: last 14d holdout
        cutoff = pd.Timestamp(date.today() - timedelta(days=14))
        train = df_s[df_s['game_date'] < cutoff]
        test = df_s[df_s['game_date'] >= cutoff]

        # Yesterday-only set (out-of-sample, trained on data before 6/22)
        yest = df_s[df_s['game_date'] == yest_date]
        train_strict = df_s[df_s['game_date'] < yest_date]

        print(f'Walk-forward train: {len(train)}  test (last 14d): {len(test)}')
        print(f'Yesterday-only test: {len(yest)}  train_strict: {len(train_strict)}')

        if len(train) < 50 or len(test) < 10:
            continue

        X_tr, y_tr = train[feats], train['label']
        X_te, y_te = test[feats], test['label']

        models = [
            ('LogReg', LogisticRegression(max_iter=2000, C=1.0)),
            ('DecisionTree', DecisionTreeClassifier(max_depth=5, min_samples_leaf=15, random_state=42)),
            ('GradientBoost', GradientBoostingClassifier(n_estimators=300, max_depth=3, learning_rate=0.05, random_state=42)),
            ('GradientBoost-deep', GradientBoostingClassifier(n_estimators=500, max_depth=5, learning_rate=0.03, random_state=42)),
            ('RandomForest', RandomForestClassifier(n_estimators=500, max_depth=8, random_state=42, min_samples_leaf=8)),
        ]

        print()
        print(f'{"Model":>20s} | {"holdout":>8s} | {"yest":>10s} | conf-UNDER (p<=.40)')
        for name, model in models:
            if name == 'LogReg':
                sc = StandardScaler()
                xs = sc.fit_transform(X_tr)
                xst = sc.transform(X_te)
                model.fit(xs, y_tr)
                pred = model.predict(xst)
                proba = model.predict_proba(xst)[:, 1]
            else:
                model.fit(X_tr, y_tr)
                pred = model.predict(X_te)
                proba = model.predict_proba(X_te)[:, 1]

            acc = accuracy_score(y_te, pred) * 100
            mask_cu = proba <= 0.40
            cu_correct = int((y_te.values[mask_cu] == 0).sum()) if mask_cu.any() else 0
            n_cu = int(mask_cu.sum())
            cu_str = f'{cu_correct}/{n_cu} ({100*cu_correct/n_cu if n_cu else 0:.0f}%)'

            # Yesterday
            yest_str = '-'
            if len(yest) > 0:
                # retrain on train_strict for fair yesterday test
                if name == 'LogReg':
                    sc2 = StandardScaler()
                    xs2 = sc2.fit_transform(train_strict[feats])
                    xy = sc2.transform(yest[feats])
                    m2 = LogisticRegression(max_iter=2000, C=1.0)
                    m2.fit(xs2, train_strict['label'])
                    yp = m2.predict(xy)
                else:
                    m2 = model.__class__(**model.get_params())
                    m2.fit(train_strict[feats], train_strict['label'])
                    yp = m2.predict(yest[feats])
                yc = int((yp == yest['label'].values).sum())
                yest_str = f'{yc}/{len(yest)} ({100*yc/len(yest):.0f}%)'

            print(f'  {name:>18s} | {acc:>6.1f}% | {yest_str:>10s} | {cu_str}')

        # Feature importance from GB-deep
        for name, model in models:
            if name == 'GradientBoost-deep':
                model.fit(X_tr, y_tr)
                imp = sorted(zip(feats, model.feature_importances_), key=lambda x: -x[1])
                print(f'\n  Top 10 features (GB-deep, set {set_name}):')
                for f, i in imp[:10]:
                    print(f'    {f:>26s}: {i*100:>5.1f}%')
                break


if __name__ == '__main__':
    main()
