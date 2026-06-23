"""Walk-forward backtest across the entire season.

For each day in the season:
  1. Train v7 totals model on all games strictly before that day
  2. Predict that day's games
  3. Compare to actuals
  4. Tally hits / misses

This is the "what would have happened in production" test.

Also reports:
  - Per-month accuracy (April vs May vs June)
  - OVER vs UNDER call distribution (the over-heavy bias check)
  - conf-tier accuracy across rolling 30d windows
  - Composite baseline for direct comparison
"""
import os
from datetime import date, timedelta
from collections import defaultdict
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import accuracy_score

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

BASE_TOT = [
    'home_sp_xera', 'away_sp_xera',
    'home_pitcher_last_3_era', 'away_pitcher_last_3_era',
    'home_bullpen_era', 'away_bullpen_era',
    'home_wrc_plus', 'away_wrc_plus',
    'park_run_factor', 'temperature',
    'projected_total', 'close_total',
    'home_score', 'away_score', 'game_date',
]

FEATS_TOT = [
    'sp_xera_min', 'sp_xera_max', 'sp_l3_min', 'sp_l3_max',
    'bp_avg', 'wrc_avg', 'park_run_factor', 'temperature',
    'gap_proj', 'day_of_week', 'line',
]


def engineer_tot(df):
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


def pull_totals():
    rows = []
    off = 0
    sel = ','.join(BASE_TOT)
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
    return rows


def main():
    print('=== Walk-forward backtest: v7 TOTALS ===')
    print('For each day in the season, train on everything before, predict that day.')
    print()

    df = pd.DataFrame(pull_totals())
    df['total'] = df['home_score'] + df['away_score']
    df['label'] = (df['total'] > df['close_total']).astype(int)
    df = df[df['total'] != df['close_total']].copy()
    df = engineer_tot(df)
    keep_cols = FEATS_TOT + ['label', 'game_date', 'total', 'projected_total']
    keep_cols = list(dict.fromkeys(keep_cols))  # dedupe preserving order
    df_clean = df[keep_cols].dropna(subset=FEATS_TOT + ['label'])
    df_clean = df_clean.loc[:, ~df_clean.columns.duplicated()].sort_values('game_date').reset_index(drop=True)
    print(f'Total graded games available: {len(df_clean)}')

    all_dates = sorted(df_clean['game_date'].unique())
    print(f'Date range: {all_dates[0].date()} to {all_dates[-1].date()} ({len(all_dates)} days)')
    print()

    # Walk-forward: need at least 21 days of training data before starting predictions
    min_train_days = all_dates[0] + pd.Timedelta(days=21)
    test_dates = [d for d in all_dates if d >= min_train_days]
    print(f'Backtesting predictions for {len(test_dates)} game days (after {min_train_days.date()})')
    print()

    # Cumulative results
    v7_results = []      # list of (date, actual, v7_pred, v7_prob, composite_pred)
    composite_results = []

    last_print = None
    for i, test_date in enumerate(test_dates):
        train = df_clean[df_clean['game_date'] < test_date]
        test = df_clean[df_clean['game_date'] == test_date]
        if len(train) < 50 or len(test) == 0: continue

        # v7 GB
        gb = GradientBoostingClassifier(n_estimators=200, max_depth=4, learning_rate=0.05, random_state=42)
        gb.fit(train[FEATS_TOT], train['label'])
        proba = gb.predict_proba(test[FEATS_TOT])[:, 1]

        for j, (idx, row) in enumerate(test.iterrows()):
            actual = int(row['label'])
            v7_p = float(proba[j])
            v7_pred = 1 if v7_p >= 0.5 else 0
            # Composite: projected_total vs line
            comp_pred = None
            if row.get('projected_total') is not None:
                gap = float(row['projected_total']) - float(row['line'])
                if gap > 0.3: comp_pred = 1
                elif gap < -0.3: comp_pred = 0
            v7_results.append({
                'date': test_date,
                'actual': actual,
                'v7_pred': v7_pred,
                'v7_prob': v7_p,
                'line': float(row['line']),
                'total': float(row['total']),
            })
            if comp_pred is not None:
                composite_results.append({
                    'date': test_date,
                    'actual': actual,
                    'comp_pred': comp_pred,
                })

        if i % 30 == 0 and i > 0:
            print(f'  [{i}/{len(test_dates)}] {test_date.date()} ...')

    v7_df = pd.DataFrame(v7_results)
    comp_df = pd.DataFrame(composite_results)

    print()
    print('=' * 80)
    print(f'WALK-FORWARD RESULTS')
    print('=' * 80)

    # Overall
    v7_acc = (v7_df['actual'] == v7_df['v7_pred']).mean() * 100
    comp_acc = (comp_df['actual'] == comp_df['comp_pred']).mean() * 100
    print(f'\nOVERALL ({len(v7_df)} predictions):')
    print(f'  v7 model:   {v7_acc:.1f}% accuracy')
    print(f'  Composite:  {comp_acc:.1f}% accuracy')

    # OVER vs UNDER call distribution (the bias check)
    print(f'\n=== OVER/UNDER CALL DISTRIBUTION (the bias check) ===')
    v7_over_calls = (v7_df['v7_pred'] == 1).sum()
    v7_under_calls = (v7_df['v7_pred'] == 0).sum()
    actual_overs = (v7_df['actual'] == 1).sum()
    actual_unders = (v7_df['actual'] == 0).sum()
    print(f'  Actual:    {actual_overs} OVER / {actual_unders} UNDER ({100*actual_overs/len(v7_df):.0f}% OVER)')
    print(f'  v7 calls:  {v7_over_calls} OVER / {v7_under_calls} UNDER ({100*v7_over_calls/len(v7_df):.0f}% OVER)')
    if len(comp_df):
        c_over = (comp_df['comp_pred'] == 1).sum()
        c_under = (comp_df['comp_pred'] == 0).sum()
        print(f'  Composite: {c_over} OVER / {c_under} UNDER ({100*c_over/len(comp_df):.0f}% OVER)')

    # Per direction hit rate
    print(f'\n=== PER-DIRECTION HIT RATE ===')
    for direction, label in [(1, 'OVER'), (0, 'UNDER')]:
        v7_dir = v7_df[v7_df['v7_pred'] == direction]
        v7_hit = (v7_dir['actual'] == direction).sum()
        v7_n = len(v7_dir)
        c_dir = comp_df[comp_df['comp_pred'] == direction]
        c_hit = (c_dir['actual'] == direction).sum()
        c_n = len(c_dir)
        print(f'  {label}:  v7 {v7_hit}/{v7_n} ({100*v7_hit/max(1,v7_n):.1f}%)  composite {c_hit}/{c_n} ({100*c_hit/max(1,c_n):.1f}%)')

    # Per-month
    print(f'\n=== PER-MONTH ACCURACY ===')
    v7_df['month'] = pd.to_datetime(v7_df['date']).dt.strftime('%Y-%m')
    comp_df['month'] = pd.to_datetime(comp_df['date']).dt.strftime('%Y-%m')
    months = sorted(v7_df['month'].unique())
    print(f'{"month":>10s} | {"v7 acc":>10s} | {"v7 OVER%":>10s} | {"actual OVER%":>14s} | {"composite acc":>14s}')
    for m in months:
        v7_m = v7_df[v7_df['month'] == m]
        c_m = comp_df[comp_df['month'] == m]
        v7_acc_m = (v7_m['actual'] == v7_m['v7_pred']).mean() * 100
        v7_over_pct = (v7_m['v7_pred'] == 1).mean() * 100
        actual_over_pct = (v7_m['actual'] == 1).mean() * 100
        c_acc_m = (c_m['actual'] == c_m['comp_pred']).mean() * 100 if len(c_m) else 0
        print(f'  {m:>8s} | {v7_acc_m:>8.1f}% | {v7_over_pct:>8.0f}% | {actual_over_pct:>12.0f}% | {c_acc_m:>12.1f}%')

    # Confidence-tier hit rates ACROSS ALL HISTORY
    print(f'\n=== v7 CONFIDENCE-TIER HIT RATE (walk-forward, all season) ===')
    print(f'{"Tier":>14s} | {"hits":>20s} | {"OVER%":>8s}')
    for low, high, label in [
        (0.00, 0.30, 'conf-UNDER (p<=.30)'),
        (0.30, 0.40, 'lean-UNDER (.30-.40)'),
        (0.40, 0.50, 'mild-UNDER (.40-.50)'),
        (0.50, 0.60, 'mild-OVER (.50-.60)'),
        (0.60, 0.70, 'lean-OVER (.60-.70)'),
        (0.70, 1.00, 'conf-OVER (p>=.70)'),
    ]:
        mask = (v7_df['v7_prob'] >= low) & (v7_df['v7_prob'] < high)
        n = int(mask.sum())
        if n == 0: continue
        if label.startswith('conf-UNDER') or label.startswith('lean-UNDER') or label.startswith('mild-UNDER'):
            wins = int((v7_df.loc[mask, 'actual'] == 0).sum())
            direction = 'U'
        else:
            wins = int((v7_df.loc[mask, 'actual'] == 1).sum())
            direction = 'O'
        print(f'  {label:>14s} | {wins}/{n} ({100*wins/n:.0f}%) {direction:>1s} | {100*(v7_df.loc[mask, "actual"] == 1).mean():>6.0f}%')

    # Rolling 30d accuracy (recent vs older)
    print(f'\n=== ROLLING 30-DAY ACCURACY ===')
    v7_df = v7_df.sort_values('date').reset_index(drop=True)
    window = 30
    print(f'{"period":>22s} | {"v7 acc":>10s} | {"n":>5s}')
    chunks = []
    for start in range(0, len(v7_df), window):
        chunk = v7_df.iloc[start:start+window]
        if len(chunk) < window // 2: continue
        acc = (chunk['actual'] == chunk['v7_pred']).mean() * 100
        chunks.append((chunk['date'].iloc[0].date(), chunk['date'].iloc[-1].date(), acc, len(chunk)))
    for d1, d2, a, n in chunks:
        print(f'  {str(d1):>10s} to {str(d2):>10s} | {a:>8.1f}% | {n:>5d}')


if __name__ == '__main__':
    main()
