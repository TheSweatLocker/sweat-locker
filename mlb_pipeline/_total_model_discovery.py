"""Total model discovery + retrain.

Goal: build a new model from scratch that predicts OVER/UNDER better
than current v3/v4/jerry composite. Validate on yesterday (where 11/12
were UNDERs) and walk-forward on full historical archive.

Approach:
  1. Pull all 917 graded games with close_total available
  2. Engineer derived features (SP averages, BP averages, gaps to line)
  3. Two feature sets:
     A: high-coverage core features (works on ~500+ rows)
     B: full feature set (works on ~250 rows)
  4. Train 4 models per set: LogReg, DecisionTree, GradientBoost, RandomForest
  5. Walk-forward validation: train on <14d-old, test on last 14d
  6. Apply to yesterday specifically to see if any model would have caught 8+/12 UNDERs
  7. Report feature importance + interpretable rules
"""
import os
from datetime import date, timedelta
import requests
from dotenv import load_dotenv
import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

CORE = [
    'home_sp_xera', 'away_sp_xera', 'home_sp_era', 'away_sp_era',
    'home_sp_k_pct', 'away_sp_k_pct',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_pitcher_last_3_k_pct', 'away_pitcher_last_3_k_pct',
    'home_bullpen_era', 'away_bullpen_era',
    'home_runs_per_game', 'away_runs_per_game',
    'home_wrc_plus', 'away_wrc_plus',
    'park_run_factor', 'temperature',
    'home_team_k_pct', 'away_team_k_pct',
    'nrfi_score', 'signal_confluence_net',
    'projected_total', 'model_pred_total',
    'close_total', 'home_score', 'away_score', 'game_date',
]


def pull_data():
    rows = []
    offset = 0
    sel = ','.join(CORE)
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


def main():
    print('Pulling all graded games...')
    rows = pull_data()
    df = pd.DataFrame(rows)
    df['total'] = df['home_score'] + df['away_score']
    df['line'] = df['close_total']
    df['label'] = (df['total'] > df['line']).astype(int)  # 1 = OVER, 0 = UNDER
    df = df[df['total'] != df['line']].copy()
    df['game_date'] = pd.to_datetime(df['game_date'])
    print(f'Total rows: {len(df)}  OVER rate: {df.label.mean()*100:.1f}%')

    # Engineered features
    df['sp_xera_avg'] = (pd.to_numeric(df['home_sp_xera'], errors='coerce') + pd.to_numeric(df['away_sp_xera'], errors='coerce')) / 2
    df['sp_xera_min'] = pd.concat([pd.to_numeric(df['home_sp_xera'], errors='coerce'), pd.to_numeric(df['away_sp_xera'], errors='coerce')], axis=1).min(axis=1)
    df['sp_xera_max'] = pd.concat([pd.to_numeric(df['home_sp_xera'], errors='coerce'), pd.to_numeric(df['away_sp_xera'], errors='coerce')], axis=1).max(axis=1)
    df['sp_l3_avg'] = (pd.to_numeric(df['home_pitcher_last_3_era'], errors='coerce') + pd.to_numeric(df['away_pitcher_last_3_era'], errors='coerce')) / 2
    df['sp_l3_min'] = pd.concat([pd.to_numeric(df['home_pitcher_last_3_era'], errors='coerce'), pd.to_numeric(df['away_pitcher_last_3_era'], errors='coerce')], axis=1).min(axis=1)
    df['sp_l3_max'] = pd.concat([pd.to_numeric(df['home_pitcher_last_3_era'], errors='coerce'), pd.to_numeric(df['away_pitcher_last_3_era'], errors='coerce')], axis=1).max(axis=1)
    df['bp_avg'] = (pd.to_numeric(df['home_bullpen_era'], errors='coerce') + pd.to_numeric(df['away_bullpen_era'], errors='coerce')) / 2
    df['wrc_avg'] = (pd.to_numeric(df['home_wrc_plus'], errors='coerce') + pd.to_numeric(df['away_wrc_plus'], errors='coerce')) / 2
    df['gap_proj'] = pd.to_numeric(df['projected_total'], errors='coerce') - df['line']
    df['gap_v4'] = pd.to_numeric(df['model_pred_total'], errors='coerce') - df['line']
    df['k_pct_avg'] = (pd.to_numeric(df['home_sp_k_pct'], errors='coerce') + pd.to_numeric(df['away_sp_k_pct'], errors='coerce')) / 2

    # Feature sets
    set_a = ['sp_xera_avg', 'sp_xera_min', 'sp_l3_min', 'bp_avg', 'wrc_avg',
             'park_run_factor', 'line', 'gap_proj']
    set_b = ['sp_xera_avg', 'sp_xera_min', 'sp_xera_max', 'sp_l3_avg', 'sp_l3_min',
             'sp_l3_max', 'bp_avg', 'wrc_avg', 'k_pct_avg',
             'park_run_factor', 'temperature', 'nrfi_score', 'signal_confluence_net',
             'gap_proj', 'gap_v4', 'line']

    for f in set_b:
        df[f] = pd.to_numeric(df[f], errors='coerce')

    print()
    print('Feature coverage:')
    for f in set_b:
        pct = df[f].notna().mean() * 100
        print(f'  {f:>22s}: {pct:>5.1f}%')

    for set_name, feats in [('A_core', set_a), ('B_full', set_b)]:
        print()
        print(f'==== FEATURE SET {set_name} ({len(feats)} features) ====')
        df_s = df[feats + ['label', 'game_date', 'total', 'line']].dropna()
        print(f'Complete rows: {len(df_s)}')
        if len(df_s) < 100:
            print('Too few rows, skipping')
            continue

        df_s = df_s.sort_values('game_date')
        cutoff = pd.Timestamp(date.today() - timedelta(days=14))
        train = df_s[df_s['game_date'] < cutoff]
        test = df_s[df_s['game_date'] >= cutoff]
        print(f'Train rows: {len(train)}  Test (last 14d): {len(test)}')
        if len(train) < 50 or len(test) < 10:
            continue

        X_train, y_train = train[feats], train['label']
        X_test, y_test = test[feats], test['label']

        results = []
        for name, model in [
            ('LogReg', LogisticRegression(max_iter=1000, C=1.0)),
            ('DecisionTree', DecisionTreeClassifier(max_depth=4, min_samples_leaf=20, random_state=42)),
            ('GradientBoost', GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)),
            ('RandomForest', RandomForestClassifier(n_estimators=300, max_depth=5, random_state=42, min_samples_leaf=10)),
        ]:
            if name == 'LogReg':
                sc = StandardScaler()
                xs = sc.fit_transform(X_train)
                xst = sc.transform(X_test)
                model.fit(xs, y_train)
                pred = model.predict(xst)
                proba = model.predict_proba(xst)[:, 1]
            else:
                model.fit(X_train, y_train)
                pred = model.predict(X_test)
                proba = model.predict_proba(X_test)[:, 1]
            acc = accuracy_score(y_test, pred) * 100
            n_over = int((pred == 1).sum())
            n_under = int((pred == 0).sum())
            mask_co = proba >= 0.60
            mask_cu = proba <= 0.40
            co_correct = ((y_test.values[mask_co] == 1)).sum() if mask_co.any() else 0
            cu_correct = ((y_test.values[mask_cu] == 0)).sum() if mask_cu.any() else 0
            n_co = int(mask_co.sum())
            n_cu = int(mask_cu.sum())
            results.append({
                'name': name, 'acc': acc, 'n_over': n_over, 'n_under': n_under,
                'co_correct': co_correct, 'n_co': n_co, 'cu_correct': cu_correct, 'n_cu': n_cu,
                'model': model,
            })

        print()
        print(f'{"Model":>14s} | last-14d acc | preds | conf-OVER (>=.60) | conf-UNDER (<=.40)')
        for r in results:
            co_str = f'{r["co_correct"]}/{r["n_co"]} ({100*r["co_correct"]/r["n_co"] if r["n_co"] else 0:.0f}%)'
            cu_str = f'{r["cu_correct"]}/{r["n_cu"]} ({100*r["cu_correct"]/r["n_cu"] if r["n_cu"] else 0:.0f}%)'
            print(f'  {r["name"]:>14s} | {r["acc"]:>9.1f}% | {r["n_over"]}O/{r["n_under"]}U | {co_str:>18s} | {cu_str:>18s}')

        # Print decision tree rules + GB feature importance
        for r in results:
            if r['name'] == 'DecisionTree':
                print()
                print(f'INTERPRETABLE RULES (DecisionTree, set {set_name}):')
                try:
                    print(export_text(r['model'], feature_names=list(feats)))
                except Exception as e:
                    print(f'  export_text failed: {e}')
            if r['name'] == 'GradientBoost':
                print(f'\nGradientBoost feature importance:')
                imp = sorted(zip(feats, r['model'].feature_importances_), key=lambda x: -x[1])
                for f, i in imp:
                    print(f'  {f:>22s}: {i*100:>5.1f}%')

        # === APPLY MODELS TO YESTERDAY SPECIFICALLY (out-of-sample) ===
        yest_date = pd.Timestamp('2026-06-22')
        train_strict = df_s[df_s['game_date'] < yest_date]
        yest = df_s[df_s['game_date'] == yest_date]
        print()
        print(f'=== APPLY {set_name} MODELS TO YESTERDAY ({len(yest)} games) ===')
        if len(yest) > 0:
            X_tr = train_strict[feats]
            y_tr = train_strict['label']
            X_y = yest[feats]
            y_y = yest['label'].values
            for name, model in [
                ('GradientBoost', GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)),
                ('DecisionTree', DecisionTreeClassifier(max_depth=4, min_samples_leaf=20, random_state=42)),
                ('RandomForest', RandomForestClassifier(n_estimators=300, max_depth=5, random_state=42, min_samples_leaf=10)),
                ('LogReg', LogisticRegression(max_iter=1000, C=1.0)),
            ]:
                if name == 'LogReg':
                    sc = StandardScaler()
                    xs = sc.fit_transform(X_tr)
                    xyy = sc.transform(X_y)
                    model.fit(xs, y_tr)
                    pred = model.predict(xyy)
                    proba = model.predict_proba(xyy)[:, 1]
                else:
                    model.fit(X_tr, y_tr)
                    pred = model.predict(X_y)
                    proba = model.predict_proba(X_y)[:, 1]
                correct = int((pred == y_y).sum())
                n_o = int((pred == 1).sum())
                n_u = int((pred == 0).sum())
                actual_o = int(y_y.sum())
                actual_u = int(len(y_y) - actual_o)
                print(f'  {name:>14s}: {correct}/{len(y_y)} ({100*correct/len(y_y):.0f}%)  pred {n_o}O/{n_u}U  actual {actual_o}O/{actual_u}U')


if __name__ == '__main__':
    main()
