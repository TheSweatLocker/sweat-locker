"""Walk-forward backtest for ML side (home/away winner)."""
import os
from datetime import date, timedelta
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

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
    'home_bullpen_era', 'away_bullpen_era',
    'home_wrc_plus', 'away_wrc_plus', 'home_ops', 'away_ops',
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
    try: v = float(ml)
    except: return None
    if v < 0: return -v / (-v + 100)
    return 100 / (v + 100)


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
    df['spread_avg'] = df[['spread_proj','spread_v4','spread_jerry']].mean(axis=1)
    df['spread_std'] = df[['spread_proj','spread_v4','spread_jerry']].std(axis=1)
    df['all3_home'] = ((df['spread_proj']>0.3)&(df['spread_v4']>0.3)&(df['spread_jerry']>0.3)).astype(int)
    df['all3_away'] = ((df['spread_proj']<-0.3)&(df['spread_v4']<-0.3)&(df['spread_jerry']<-0.3)).astype(int)
    df['market_p_home'] = df['home_ml_close'].apply(implied_p)
    df['close_spread_signed'] = df['close_spread']
    return df


def main():
    print('=== Walk-forward backtest: v1 ML ===')
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
    keep = list(dict.fromkeys(FEATS + ['label', 'game_date', 'projected_spread']))
    df_s = df[keep].dropna(subset=FEATS + ['label'])
    df_s = df_s.loc[:, ~df_s.columns.duplicated()].sort_values('game_date').reset_index(drop=True)
    print(f'Clean rows: {len(df_s)}')

    all_dates = sorted(df_s['game_date'].unique())
    print(f'Date range: {all_dates[0].date()} to {all_dates[-1].date()}')
    min_train_days = all_dates[0] + pd.Timedelta(days=21)
    test_dates = [d for d in all_dates if d >= min_train_days]
    print(f'Backtest days: {len(test_dates)}')
    print()

    v1_results = []
    comp_results = []
    for i, td in enumerate(test_dates):
        train = df_s[df_s['game_date'] < td]
        test = df_s[df_s['game_date'] == td]
        if len(train) < 30 or len(test) == 0: continue
        model = xgb.XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, eval_metric='logloss', random_state=42)
        model.fit(train[FEATS], train['label'])
        proba = model.predict_proba(test[FEATS])[:, 1]
        for j, (idx, row) in enumerate(test.iterrows()):
            actual = int(row['label'])
            v1_p = float(proba[j])
            v1_pred = 1 if v1_p >= 0.5 else 0
            comp_pred = None
            ps = row.get('projected_spread')
            if ps is not None:
                ps = float(ps)
                if ps > 0.3: comp_pred = 1
                elif ps < -0.3: comp_pred = 0
            v1_results.append({'date': td, 'actual': actual, 'v1_pred': v1_pred, 'v1_prob': v1_p})
            if comp_pred is not None:
                comp_results.append({'date': td, 'actual': actual, 'comp_pred': comp_pred})

    v1_df = pd.DataFrame(v1_results)
    comp_df = pd.DataFrame(comp_results)

    print('=' * 80)
    print('WALK-FORWARD RESULTS (ML)')
    print('=' * 80)
    v1_acc = (v1_df['actual'] == v1_df['v1_pred']).mean() * 100
    comp_acc = (comp_df['actual'] == comp_df['comp_pred']).mean() * 100
    print(f'\nOVERALL ({len(v1_df)} preds):')
    print(f'  v1 model:   {v1_acc:.1f}%')
    print(f'  Composite:  {comp_acc:.1f}%')

    print(f'\n=== HOME/AWAY CALL DISTRIBUTION ===')
    actual_home = (v1_df['actual'] == 1).sum()
    v1_home = (v1_df['v1_pred'] == 1).sum()
    print(f'  Actual:    {actual_home}/{len(v1_df)} HOME ({100*actual_home/len(v1_df):.0f}%)')
    print(f'  v1 calls:  {v1_home}/{len(v1_df)} HOME ({100*v1_home/len(v1_df):.0f}%)')
    c_home = (comp_df['comp_pred'] == 1).sum()
    print(f'  Composite: {c_home}/{len(comp_df)} HOME ({100*c_home/len(comp_df):.0f}%)')

    print(f'\n=== PER-DIRECTION HIT RATE ===')
    for d, label in [(1, 'HOME'), (0, 'AWAY')]:
        v1_d = v1_df[v1_df['v1_pred'] == d]
        v1_hit = (v1_d['actual'] == d).sum()
        c_d = comp_df[comp_df['comp_pred'] == d]
        c_hit = (c_d['actual'] == d).sum()
        print(f'  {label}:  v1 {v1_hit}/{len(v1_d)} ({100*v1_hit/max(1,len(v1_d)):.1f}%)  composite {c_hit}/{len(c_d)} ({100*c_hit/max(1,len(c_d)):.1f}%)')

    print(f'\n=== PER-MONTH ===')
    v1_df['month'] = pd.to_datetime(v1_df['date']).dt.strftime('%Y-%m')
    comp_df['month'] = pd.to_datetime(comp_df['date']).dt.strftime('%Y-%m')
    for m in sorted(v1_df['month'].unique()):
        v1m = v1_df[v1_df['month'] == m]
        cm = comp_df[comp_df['month'] == m]
        print(f'  {m}: v1 {(v1m["actual"]==v1m["v1_pred"]).mean()*100:.1f}% | composite {(cm["actual"]==cm["comp_pred"]).mean()*100:.1f}% | actual HOME {(v1m["actual"]==1).mean()*100:.0f}%')

    print(f'\n=== v1 CONFIDENCE-TIER HIT RATE (walk-forward, all season) ===')
    for low, high, label in [
        (0.00, 0.30, 'conf-AWAY (<=.30)'),
        (0.30, 0.40, 'lean-AWAY (.30-.40)'),
        (0.40, 0.50, 'mild-AWAY (.40-.50)'),
        (0.50, 0.60, 'mild-HOME (.50-.60)'),
        (0.60, 0.70, 'lean-HOME (.60-.70)'),
        (0.70, 1.00, 'conf-HOME (>=.70)'),
    ]:
        mask = (v1_df['v1_prob'] >= low) & (v1_df['v1_prob'] < high)
        n = int(mask.sum())
        if n == 0: continue
        direction = 1 if 'HOME' in label else 0
        wins = int((v1_df.loc[mask, 'actual'] == direction).sum())
        print(f'  {label}: {wins}/{n} ({100*wins/n:.0f}%)')


if __name__ == '__main__':
    main()
