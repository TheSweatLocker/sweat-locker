"""ML model retrain — discovery v1.

Mirrors the totals retrain methodology but target = home_win (1/0).

Steps:
  1. Pull 917+ graded games with home_score, away_score, and all features.
  2. Engineer features (SP avgs, BP gap, offense gap, market signals).
  3. Walk-forward 14d holdout + yesterday-only test.
  4. Run LogReg, DT, GB, GB-deep, RF, XGBoost.
  5. Report confidence buckets (conf-HOME at p>=0.60, conf-AWAY at p<=0.40).
  6. Identify top features and ceiling.
"""
import os
from datetime import date, timedelta
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
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
    'home_ops', 'away_ops',
    'home_team_k_pct', 'away_team_k_pct',
    'park_run_factor', 'temperature',
    'home_pitcher_vs_team_era', 'away_pitcher_vs_team_era',
    'projected_total', 'projected_spread',
    'model_pred_total', 'model_pred_spread',
    'jerry_pred_total', 'jerry_pred_spread',
    'close_total', 'close_spread', 'home_ml_close', 'away_ml_close',
    'home_score', 'away_score', 'game_date',
]


def implied_p(ml):
    """Convert American odds to implied probability."""
    try:
        ml = float(ml)
    except (TypeError, ValueError):
        return None
    if ml < 0:
        return -ml / (-ml + 100)
    return 100 / (ml + 100)


def main():
    print('=== ML retrain v1 discovery ===')
    print('Pulling 917+ graded games + ML market + spread features...')
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
    for c in [col for col in df.columns if col != 'game_date']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['game_date'] = pd.to_datetime(df['game_date'])

    # Target: home wins
    df['label'] = (df['home_score'] > df['away_score']).astype(int)
    df = df[df['home_score'] != df['away_score']].copy()  # drop ties (rare/impossible in MLB)
    print(f'Rows: {len(df)}  HOME win rate: {df.label.mean()*100:.1f}%')

    # Engineered features
    df['sp_xera_diff'] = df['away_sp_xera'] - df['home_sp_xera']  # higher = home SP better
    df['sp_l3_diff'] = df['away_pitcher_last_3_era'] - df['home_pitcher_last_3_era']
    df['sp_k_pct_diff'] = df['home_sp_k_pct'] - df['away_sp_k_pct']
    df['sp_whiff_diff'] = df['home_sp_whiff_rate'] - df['away_sp_whiff_rate']
    df['bp_diff'] = df['away_bullpen_era'] - df['home_bullpen_era']  # higher = home bp better
    df['wrc_diff'] = df['home_wrc_plus'] - df['away_wrc_plus']
    df['ops_diff'] = df['home_ops'] - df['away_ops']
    df['k_pct_diff'] = df['away_team_k_pct'] - df['home_team_k_pct']  # higher = home offense punches out less
    df['pvt_era_diff'] = df['away_pitcher_vs_team_era'] - df['home_pitcher_vs_team_era']
    df['day_of_week'] = df['game_date'].dt.dayofweek
    df['month'] = df['game_date'].dt.month

    # Spread model agreement
    df['spread_proj'] = df['projected_spread']
    df['spread_v4'] = df['model_pred_spread']
    df['spread_jerry'] = df['jerry_pred_spread']
    # Convention: positive = home wins per earlier analysis
    df['spread_avg'] = df[['spread_proj', 'spread_v4', 'spread_jerry']].mean(axis=1)
    df['spread_std'] = df[['spread_proj', 'spread_v4', 'spread_jerry']].std(axis=1)
    df['spread_min'] = df[['spread_proj', 'spread_v4', 'spread_jerry']].min(axis=1)
    df['spread_max'] = df[['spread_proj', 'spread_v4', 'spread_jerry']].max(axis=1)
    df['all3_home'] = ((df['spread_proj'] > 0.3) & (df['spread_v4'] > 0.3) & (df['spread_jerry'] > 0.3)).astype(int)
    df['all3_away'] = ((df['spread_proj'] < -0.3) & (df['spread_v4'] < -0.3) & (df['spread_jerry'] < -0.3)).astype(int)

    # Market signals
    df['market_p_home'] = df['home_ml_close'].apply(implied_p)
    df['ml_juice'] = abs(df['home_ml_close'])  # absolute price magnitude
    df['close_spread_signed'] = df['close_spread']  # negative = home favored

    # Set A: high-coverage core
    set_A = ['sp_xera_diff', 'sp_l3_diff', 'sp_k_pct_diff',
             'bp_diff', 'wrc_diff', 'ops_diff',
             'spread_proj', 'market_p_home', 'close_spread_signed',
             'park_run_factor', 'day_of_week']

    # Set B: + spread model fusion
    set_B = set_A + ['spread_avg', 'spread_std', 'all3_home', 'all3_away', 'spread_v4', 'spread_jerry']

    # Set C: + mastery + pitcher detail
    set_C = set_B + ['pvt_era_diff', 'sp_whiff_diff']

    yest_date = pd.Timestamp('2026-06-22')

    for set_name, feats in [
        ('A_core (11f)', set_A),
        ('B_with_spread_fusion (17f)', set_B),
        ('C_with_mastery (19f)', set_C),
    ]:
        feats = [f for f in feats if f in df.columns]
        extra = [c for c in ['label', 'game_date', 'home_ml_close', 'home_score', 'away_score'] if c not in feats]
        df_s = df[feats + extra].dropna(subset=feats + ['label'])
        df_s = df_s.loc[:, ~df_s.columns.duplicated()].sort_values('game_date')

        print()
        print('=' * 100)
        print(f'SET: {set_name} ({len(feats)} feats, {len(df_s)} complete rows)')
        print('=' * 100)
        if len(df_s) < 100:
            print('Too few rows, skipping')
            continue

        cutoff = pd.Timestamp(date.today() - timedelta(days=14))
        train = df_s[df_s['game_date'] < cutoff]
        test = df_s[df_s['game_date'] >= cutoff]
        yest = df_s[df_s['game_date'] == yest_date]
        train_strict = df_s[df_s['game_date'] < yest_date]
        print(f'Train: {len(train)}  Holdout 14d: {len(test)}  Yesterday: {len(yest)}')

        if len(train) < 50: continue

        X_tr, y_tr = train[feats], train['label']
        X_te, y_te = test[feats], test['label']

        models = [
            ('LogReg', LogisticRegression(max_iter=2000, C=1.0)),
            ('LogReg-C0.3', LogisticRegression(max_iter=2000, C=0.3)),
            ('GradientBoost', GradientBoostingClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)),
            ('GradientBoost-deep', GradientBoostingClassifier(n_estimators=500, max_depth=5, learning_rate=0.03, random_state=42)),
            ('RandomForest', RandomForestClassifier(n_estimators=500, max_depth=8, random_state=42, min_samples_leaf=8)),
        ]
        if HAS_XGB:
            models.append(('XGBoost', xgb.XGBClassifier(n_estimators=500, max_depth=4, learning_rate=0.03, eval_metric='logloss', random_state=42)))

        print()
        print(f'{"Model":>22s} | {"holdout":>8s} | {"yest":>10s} | {"conf-HOME (>=.60)":>18s} | {"conf-AWAY (<=.40)":>18s}')
        for name, model in models:
            try:
                if name.startswith('LogReg'):
                    sc = StandardScaler(); xs = sc.fit_transform(X_tr); xst = sc.transform(X_te)
                    model.fit(xs, y_tr); pred = model.predict(xst); proba = model.predict_proba(xst)[:, 1]
                else:
                    model.fit(X_tr, y_tr); pred = model.predict(X_te); proba = model.predict_proba(X_te)[:, 1]
            except Exception as e:
                print(f'  {name:>20s} | ERROR: {str(e)[:60]}'); continue
            acc = accuracy_score(y_te, pred) * 100
            m_co = proba >= 0.60; m_cu = proba <= 0.40
            co_c = int((y_te.values[m_co] == 1).sum()) if m_co.any() else 0
            cu_c = int((y_te.values[m_cu] == 0).sum()) if m_cu.any() else 0
            co_str = f'{co_c}/{int(m_co.sum())} ({100*co_c/max(1,int(m_co.sum())):.0f}%)'
            cu_str = f'{cu_c}/{int(m_cu.sum())} ({100*cu_c/max(1,int(m_cu.sum())):.0f}%)'
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
            print(f'  {name:>20s} | {acc:>6.1f}% | {ystr:>10s} | {co_str:>18s} | {cu_str:>18s}')

        # Feature importance
        gb = GradientBoostingClassifier(n_estimators=500, max_depth=5, learning_rate=0.03, random_state=42)
        gb.fit(X_tr, y_tr)
        imp = sorted(zip(feats, gb.feature_importances_), key=lambda x: -x[1])
        print(f'\n  Top 10 features (GB-deep):')
        for f, i in imp[:10]:
            print(f'    {f:>26s}: {i*100:>5.1f}%')


if __name__ == '__main__':
    main()
