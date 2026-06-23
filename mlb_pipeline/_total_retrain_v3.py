"""Total model retrain v3 — extract interpretable rules + try XGBoost.

Goals:
  1. Print the DecisionTree-minimal rules that hit 9/12 yesterday
  2. Try XGBoost (typically beats sklearn GBM by 2-4%)
  3. Train LightGBM as alternative
  4. Try ensemble of 3-5 models for stability
  5. Identify the most robust model for production (high holdout + high yesterday)
"""
import os
from datetime import date, timedelta
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier, VotingClassifier
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False

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
    'projected_total', 'model_pred_total', 'jerry_pred_total',
    'close_total', 'home_score', 'away_score', 'game_date',
]


def pull_all():
    rows = []; offset = 0
    sel = ','.join(BASE_COLS)
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?home_score=not.is.null'
            f'&close_total=not.is.null&select={sel}'
            f'&order=game_date.desc&limit=1000&offset={offset}',
            headers=H, timeout=30,
        )
        chunk = r.json()
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        offset += 1000
    return rows


def engineer(df):
    for c in [col for col in df.columns if col != 'game_date']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['game_date'] = pd.to_datetime(df['game_date'])
    df['day_of_week'] = df['game_date'].dt.dayofweek
    df['is_sunday'] = (df['day_of_week'] == 6).astype(int)
    df['month'] = df['game_date'].dt.month
    df['sp_form_delta_max'] = (df['home_pitcher_last_3_era'] - df['home_sp_xera']).combine(
        df['away_pitcher_last_3_era'] - df['away_sp_xera'], max)
    df['sp_xera_avg'] = (df['home_sp_xera'] + df['away_sp_xera']) / 2
    df['sp_xera_min'] = df[['home_sp_xera', 'away_sp_xera']].min(axis=1)
    df['sp_xera_max'] = df[['home_sp_xera', 'away_sp_xera']].max(axis=1)
    df['sp_l3_min'] = df[['home_pitcher_last_3_era', 'away_pitcher_last_3_era']].min(axis=1)
    df['sp_l3_max'] = df[['home_pitcher_last_3_era', 'away_pitcher_last_3_era']].max(axis=1)
    df['sp_k_pct_avg'] = (df['home_sp_k_pct'] + df['away_sp_k_pct']) / 2
    df['bp_avg'] = (df['home_bullpen_era'] + df['away_bullpen_era']) / 2
    df['wrc_avg'] = (df['home_wrc_plus'] + df['away_wrc_plus']) / 2
    df['gap_proj'] = df['projected_total'] - df['close_total']
    df['composite_avg'] = df[['projected_total', 'model_pred_total', 'jerry_pred_total']].mean(axis=1)
    df['line'] = df['close_total']
    return df


def main():
    print('Pulling 917 graded games...')
    rows = pull_all()
    df = pd.DataFrame(rows)
    df['total'] = df['home_score'] + df['away_score']
    df['label'] = (df['total'] > df['close_total']).astype(int)
    df = df[df['total'] != df['close_total']].copy()
    df = engineer(df)

    yest_date = pd.Timestamp('2026-06-22')

    # Best balanced set from v2 findings
    feats = [
        'sp_xera_min', 'sp_xera_max', 'sp_l3_min', 'sp_l3_max',
        'sp_k_pct_avg', 'bp_avg', 'wrc_avg',
        'park_run_factor', 'temperature',
        'gap_proj', 'composite_avg',
        'day_of_week', 'is_sunday', 'month',
        'line',
    ]

    extra_cols = [c for c in ['label', 'game_date', 'total', 'line'] if c not in feats]
    df_s = df[feats + extra_cols].dropna()
    # Drop duplicate columns just in case
    df_s = df_s.loc[:, ~df_s.columns.duplicated()]
    df_s = df_s.sort_values('game_date')
    print(f'Complete rows: {len(df_s)}  OVER rate: {df_s.label.mean()*100:.1f}%')

    cutoff = pd.Timestamp(date.today() - timedelta(days=14))
    train = df_s[df_s['game_date'] < cutoff]
    test = df_s[df_s['game_date'] >= cutoff]
    yest = df_s[df_s['game_date'] == yest_date]
    train_strict = df_s[df_s['game_date'] < yest_date]

    X_tr, y_tr = train[feats], train['label']
    X_te, y_te = test[feats], test['label']

    print(f'Walk-forward train: {len(train)}  test (last 14d): {len(test)}')
    print(f'Yesterday: {len(yest)}, strict train: {len(train_strict)}')
    print()

    # === MINIMAL DECISION TREE RULES (the 9/12 yesterday winner) ===
    minimal_feats = ['sp_xera_min', 'sp_xera_max', 'bp_avg', 'wrc_avg', 'park_run_factor', 'line']
    m_extra = [c for c in ['label', 'game_date', 'total', 'line'] if c not in minimal_feats]
    df_m = df[minimal_feats + m_extra].dropna().sort_values('game_date')
    df_m = df_m.loc[:, ~df_m.columns.duplicated()]
    train_m = df_m[df_m['game_date'] < yest_date]
    yest_m = df_m[df_m['game_date'] == yest_date]
    dt_min = DecisionTreeClassifier(max_depth=5, min_samples_leaf=15, random_state=42)
    dt_min.fit(train_m[minimal_feats], train_m['label'])
    pred_yest_m = dt_min.predict(yest_m[minimal_feats])
    correct_m = (pred_yest_m == yest_m['label'].values).sum()
    print(f'== DecisionTree (minimal 6) — RULES (yesterday: {correct_m}/{len(yest_m)}) ==')
    try:
        print(export_text(dt_min, feature_names=list(minimal_feats)))
    except Exception as e:
        print(f'  (export_text bug: {e})')
        # Manual rules print
        tree = dt_min.tree_
        def walk(node, depth):
            indent = '  ' * depth
            if tree.feature[node] != -2:
                fname = minimal_feats[tree.feature[node]] if tree.feature[node] < len(minimal_feats) else f'f{tree.feature[node]}'
                thr = tree.threshold[node]
                print(f'{indent}|--- {fname} <= {thr:.2f}')
                walk(tree.children_left[node], depth + 1)
                print(f'{indent}|--- {fname} >  {thr:.2f}')
                walk(tree.children_right[node], depth + 1)
            else:
                vals = tree.value[node][0]
                cls = 'OVER' if vals[1] > vals[0] else 'UNDER'
                print(f'{indent}|--- predict {cls}  (samples: U={int(vals[0])}, O={int(vals[1])})')
        walk(0, 0)
    print()

    # === ALL MODELS — including XGBoost and LightGBM if available ===
    models = [
        ('LogReg', LogisticRegression(max_iter=2000, C=1.0)),
        ('DecisionTree', DecisionTreeClassifier(max_depth=5, min_samples_leaf=15, random_state=42)),
        ('GradientBoost', GradientBoostingClassifier(n_estimators=300, max_depth=3, learning_rate=0.05, random_state=42)),
        ('GradientBoost-deep', GradientBoostingClassifier(n_estimators=500, max_depth=5, learning_rate=0.03, random_state=42)),
        ('RandomForest', RandomForestClassifier(n_estimators=500, max_depth=8, random_state=42, min_samples_leaf=8)),
    ]
    if HAS_XGB:
        models.append(('XGBoost', xgb.XGBClassifier(n_estimators=500, max_depth=4, learning_rate=0.03, eval_metric='logloss', use_label_encoder=False, random_state=42)))
    if HAS_LGB:
        models.append(('LightGBM', lgb.LGBMClassifier(n_estimators=500, max_depth=-1, num_leaves=31, learning_rate=0.03, random_state=42, verbose=-1)))

    print(f'== SOLID SET ({len(feats)} features) — full benchmark ==')
    print(f'{"Model":>22s} | {"holdout":>8s} | {"yest":>10s} | {"conf-UNDER (<=.40)":>22s} | {"conf-OVER (>=.60)":>22s}')
    print('-' * 105)
    holdout_results = []
    for name, model in models:
        if name == 'LogReg':
            sc = StandardScaler(); xs = sc.fit_transform(X_tr); xst = sc.transform(X_te)
            model.fit(xs, y_tr); pred = model.predict(xst); proba = model.predict_proba(xst)[:, 1]
        else:
            model.fit(X_tr, y_tr); pred = model.predict(X_te); proba = model.predict_proba(X_te)[:, 1]
        acc = accuracy_score(y_te, pred) * 100
        m_cu = proba <= 0.40; m_co = proba >= 0.60
        cu_c = int((y_te.values[m_cu] == 0).sum()) if m_cu.any() else 0
        co_c = int((y_te.values[m_co] == 1).sum()) if m_co.any() else 0
        cu_str = f'{cu_c}/{int(m_cu.sum())} ({100*cu_c/max(1, int(m_cu.sum())):.0f}%)'
        co_str = f'{co_c}/{int(m_co.sum())} ({100*co_c/max(1, int(m_co.sum())):.0f}%)'

        # Yesterday
        if len(yest):
            if name == 'LogReg':
                sc2 = StandardScaler(); xs2 = sc2.fit_transform(train_strict[feats]); xy = sc2.transform(yest[feats])
                m2 = LogisticRegression(max_iter=2000, C=1.0); m2.fit(xs2, train_strict['label']); yp = m2.predict(xy)
            else:
                m2 = model.__class__(**model.get_params())
                m2.fit(train_strict[feats], train_strict['label']); yp = m2.predict(yest[feats])
            yc = int((yp == yest['label'].values).sum())
            ystr = f'{yc}/{len(yest)} ({100*yc/len(yest):.0f}%)'
        else:
            ystr = '-'
        holdout_results.append((name, acc, ystr, cu_str, co_str))
        print(f'  {name:>20s} | {acc:>6.1f}% | {ystr:>10s} | {cu_str:>22s} | {co_str:>22s}')

    # === ENSEMBLE OF TOP 3 ===
    print()
    print('== ENSEMBLE (LogReg + GB-deep + RandomForest) ==')
    sc = StandardScaler()
    xs_tr = sc.fit_transform(X_tr); xs_te = sc.transform(X_te)
    lr = LogisticRegression(max_iter=2000, C=1.0).fit(xs_tr, y_tr)
    gb = GradientBoostingClassifier(n_estimators=500, max_depth=5, learning_rate=0.03, random_state=42).fit(X_tr, y_tr)
    rf = RandomForestClassifier(n_estimators=500, max_depth=8, random_state=42, min_samples_leaf=8).fit(X_tr, y_tr)
    p_lr = lr.predict_proba(xs_te)[:, 1]
    p_gb = gb.predict_proba(X_te)[:, 1]
    p_rf = rf.predict_proba(X_te)[:, 1]
    p_ens = (p_lr + p_gb + p_rf) / 3
    pred_ens = (p_ens >= 0.5).astype(int)
    acc_ens = accuracy_score(y_te, pred_ens) * 100
    m_cu = p_ens <= 0.40; m_co = p_ens >= 0.60
    cu_c = int((y_te.values[m_cu] == 0).sum()) if m_cu.any() else 0
    co_c = int((y_te.values[m_co] == 1).sum()) if m_co.any() else 0
    print(f'  Ensemble holdout: {acc_ens:.1f}%  conf-UNDER {cu_c}/{int(m_cu.sum())}  conf-OVER {co_c}/{int(m_co.sum())}')

    # Ensemble on yesterday
    if len(yest):
        sc2 = StandardScaler()
        xs2_tr = sc2.fit_transform(train_strict[feats]); xs2_y = sc2.transform(yest[feats])
        lr2 = LogisticRegression(max_iter=2000, C=1.0).fit(xs2_tr, train_strict['label'])
        gb2 = GradientBoostingClassifier(n_estimators=500, max_depth=5, learning_rate=0.03, random_state=42).fit(train_strict[feats], train_strict['label'])
        rf2 = RandomForestClassifier(n_estimators=500, max_depth=8, random_state=42, min_samples_leaf=8).fit(train_strict[feats], train_strict['label'])
        p_lr_y = lr2.predict_proba(xs2_y)[:, 1]
        p_gb_y = gb2.predict_proba(yest[feats])[:, 1]
        p_rf_y = rf2.predict_proba(yest[feats])[:, 1]
        p_ens_y = (p_lr_y + p_gb_y + p_rf_y) / 3
        pred_y = (p_ens_y >= 0.5).astype(int)
        yc = int((pred_y == yest['label'].values).sum())
        print(f'  Ensemble yesterday: {yc}/{len(yest)} ({100*yc/len(yest):.0f}%)')
        # Per-game ensemble detail
        print(f'\n  Per-game ensemble on yesterday:')
        for (idx, row), prob, pp in zip(yest.iterrows(), p_ens_y, pred_y):
            actual = 'OVER' if row['label'] == 1 else 'UNDER'
            pred_lbl = 'OVER' if pp == 1 else 'UNDER'
            mark = '✓' if actual == pred_lbl else '✗'
            print(f'    {mark}  line={row["line"]:.1f}  total={int(row["total"])}  actual={actual:5s}  pred={pred_lbl:5s} p_over={prob:.2f}')


if __name__ == '__main__':
    main()
