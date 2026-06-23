"""Walk-forward backtest sliced by TIER (the question that was never asked).

For each historical game:
  - Composite direction: OVER if projected_total > line+0.3, UNDER if <line-0.3
  - Composite magnitude tier: how far model is from line (gap)
  - Model unity: how many of v3/v4/jerry agree on direction
  - signal_confluence_net (cohort layer signal)
  - sweat_score / sweat_tier (where populated)

Then slice hit rate by each tier dimension to answer:
  Does ELITE really hit higher than STRONG? Does cohort confluence add edge?
"""
import os
from collections import defaultdict
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

COLS = [
    'game_date', 'home_team', 'away_team',
    'close_total', 'home_score', 'away_score',
    'projected_total', 'model_pred_total', 'jerry_pred_total',
    'signal_confluence_net',
    'close_spread', 'projected_spread', 'model_pred_spread', 'jerry_pred_spread',
    'home_ml_close',
]


def pull():
    rows = []; off = 0
    sel = ','.join(COLS)
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?home_score=not.is.null'
            f'&close_total=not.is.null&projected_total=not.is.null'
            f'&select={sel}&order=game_date.desc&limit=1000&offset={off}',
            headers=H, timeout=30,
        )
        chunk = r.json()
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


def main():
    df = pd.DataFrame(pull())
    for c in [col for col in df.columns if col not in ('game_date', 'home_team', 'away_team')]:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['total'] = df['home_score'] + df['away_score']
    df['label_over'] = (df['total'] > df['close_total']).astype(int)
    df = df[df['total'] != df['close_total']].copy()
    df['game_date'] = pd.to_datetime(df['game_date'])

    df['gap_proj'] = df['projected_total'] - df['close_total']
    df['gap_v4'] = df['model_pred_total'] - df['close_total']
    df['gap_jerry'] = df['jerry_pred_total'] - df['close_total']
    df['composite_avg'] = df[['projected_total', 'model_pred_total', 'jerry_pred_total']].mean(axis=1)
    df['gap_composite'] = df['composite_avg'] - df['close_total']
    df['models_over'] = ((df['gap_proj'] > 0.3).astype(int) + (df['gap_v4'] > 0.3).astype(int) + (df['gap_jerry'] > 0.3).astype(int))
    df['models_under'] = ((df['gap_proj'] < -0.3).astype(int) + (df['gap_v4'] < -0.3).astype(int) + (df['gap_jerry'] < -0.3).astype(int))

    print(f'Total games in backtest: {len(df)}')
    print(f'Overall OVER rate: {df.label_over.mean()*100:.1f}%')
    print()

    # === SLICE 1: by composite gap magnitude (PROXY for tier) ===
    print('=' * 90)
    print('1) HIT RATE BY COMPOSITE GAP MAGNITUDE (the "tier" question)')
    print('=' * 90)
    print('\nOVER direction (gap_proj > 0):')
    print(f'{"Gap band":>16s} | {"n":>5s} | {"OVER hit":>14s} | {"OVER %":>8s}')
    for lo, hi, label in [(0.3, 0.7, 'mild (.3-.7)'),
                          (0.7, 1.2, 'lean (.7-1.2)'),
                          (1.2, 2.0, 'strong (1.2-2.0)'),
                          (2.0, 3.0, 'prime (2.0-3.0)'),
                          (3.0, 99, 'elite (3.0+)')]:
        sub = df[(df['gap_proj'] >= lo) & (df['gap_proj'] < hi)]
        if len(sub) == 0: continue
        hits = int(sub['label_over'].sum())
        n = len(sub)
        print(f'  {label:>14s} | {n:>5d} | {hits}/{n} | {100*hits/n:>6.1f}%')

    print('\nUNDER direction (gap_proj < 0):')
    print(f'{"Gap band":>16s} | {"n":>5s} | {"UNDER hit":>14s} | {"UNDER %":>8s}')
    for lo, hi, label in [(-0.7, -0.3, 'mild (-.3 to -.7)'),
                          (-1.2, -0.7, 'lean (-.7 to -1.2)'),
                          (-2.0, -1.2, 'strong (-1.2 to -2.0)'),
                          (-3.0, -2.0, 'prime (-2.0 to -3.0)'),
                          (-99, -3.0, 'elite (-3.0 and below)')]:
        sub = df[(df['gap_proj'] >= lo) & (df['gap_proj'] < hi)]
        if len(sub) == 0: continue
        unders = int((sub['label_over'] == 0).sum())
        n = len(sub)
        print(f'  {label:>16s} | {n:>5d} | {unders}/{n} | {100*unders/n:>6.1f}%')

    # === SLICE 2: model unity (how many of 3 agree) ===
    print()
    print('=' * 90)
    print('2) HIT RATE BY MODEL UNITY (all 3 agree vs 2 of 3 etc)')
    print('=' * 90)
    for tag, mask in [
        ('ALL 3 OVER (4-way unanimous if v5 also)', df['models_over'] == 3),
        ('ALL 3 UNDER', df['models_under'] == 3),
        ('2 of 3 OVER', df['models_over'] == 2),
        ('2 of 3 UNDER', df['models_under'] == 2),
        ('1 of 3 OVER', df['models_over'] == 1),
        ('1 of 3 UNDER', df['models_under'] == 1),
        ('0 (no direction)', (df['models_over'] == 0) & (df['models_under'] == 0)),
    ]:
        sub = df[mask]
        n = len(sub)
        if n == 0: continue
        if 'OVER' in tag and 'no direction' not in tag:
            hits = int(sub['label_over'].sum())
            label = 'OVER'
        elif 'UNDER' in tag:
            hits = int((sub['label_over'] == 0).sum())
            label = 'UNDER'
        else:
            hits = int(sub['label_over'].sum())
            label = 'OVER'
        print(f'  {tag:>42s} | n={n:>5d} | {hits}/{n} {label} ({100*hits/n:.1f}%)')

    # === SLICE 3: signal_confluence_net (cohort layer) ===
    print()
    print('=' * 90)
    print('3) HIT RATE BY signal_confluence_net (cohort layer)')
    print('=' * 90)
    for lo, hi, label in [(-99, -5, 'snc <= -5 (loud cohort UNDER)'),
                          (-5, -3, 'snc -5 to -3 (cohort UNDER)'),
                          (-3, -1, 'snc -3 to -1 (mild UNDER)'),
                          (-1, 1, 'snc -1 to 1 (neutral)'),
                          (1, 3, 'snc 1 to 3 (mild OVER)'),
                          (3, 5, 'snc 3 to 5 (cohort OVER)'),
                          (5, 99, 'snc >= 5 (loud cohort OVER)')]:
        sub = df[(df['signal_confluence_net'] >= lo) & (df['signal_confluence_net'] < hi)]
        if len(sub) == 0: continue
        n = len(sub)
        over_rate = sub['label_over'].mean() * 100
        print(f'  {label:>34s} | n={n:>4d} | OVER {over_rate:>5.1f}%')

    # === SLICE 4: composite gap PLUS model unity ===
    print()
    print('=' * 90)
    print('4) COMBINED: composite gap MAGNITUDE + ALL-3 unanimous (highest-tier filter)')
    print('=' * 90)
    print('\nALL 3 models OVER + gap_composite magnitude:')
    for lo, hi, label in [(0.3, 1.0, 'mild + all3'),
                          (1.0, 2.0, 'strong + all3'),
                          (2.0, 99, 'elite + all3')]:
        sub = df[(df['models_over'] == 3) & (df['gap_composite'] >= lo) & (df['gap_composite'] < hi)]
        n = len(sub)
        if n == 0: continue
        hits = int(sub['label_over'].sum())
        print(f'  {label:>22s} | n={n:>4d} | OVER hit {hits}/{n} ({100*hits/n:.1f}%)')

    print('\nALL 3 models UNDER + gap_composite magnitude:')
    for lo, hi, label in [(-1.0, -0.3, 'mild + all3'),
                          (-2.0, -1.0, 'strong + all3'),
                          (-99, -2.0, 'elite + all3')]:
        sub = df[(df['models_under'] == 3) & (df['gap_composite'] >= lo) & (df['gap_composite'] < hi)]
        n = len(sub)
        if n == 0: continue
        hits = int((sub['label_over'] == 0).sum())
        print(f'  {label:>22s} | n={n:>4d} | UNDER hit {hits}/{n} ({100*hits/n:.1f}%)')

    # === SLICE 5: snc + model unity COMBINED (true ELITE filter) ===
    print()
    print('=' * 90)
    print('5) FULL FILTER: all 3 models AGREE + cohort agrees (true ELITE)')
    print('=' * 90)
    print('\nOVER side combos:')
    for snc_lo, snc_hi, snc_label in [(3, 5, 'snc 3-5'),
                                       (5, 99, 'snc 5+')]:
        for mag_lo, mag_hi, mag_label in [(0.5, 1.5, 'gap .5-1.5'),
                                           (1.5, 99, 'gap 1.5+')]:
            sub = df[(df['models_over'] == 3) &
                     (df['signal_confluence_net'] >= snc_lo) &
                     (df['signal_confluence_net'] < snc_hi) &
                     (df['gap_composite'] >= mag_lo) &
                     (df['gap_composite'] < mag_hi)]
            n = len(sub)
            if n < 5: continue
            hits = int(sub['label_over'].sum())
            print(f'  all3 + {snc_label} + {mag_label}: n={n}, OVER {hits}/{n} ({100*hits/n:.1f}%)')
    print('\nUNDER side combos:')
    for snc_lo, snc_hi, snc_label in [(-99, -3, 'snc <= -3'),
                                       (-99, -5, 'snc <= -5')]:
        for mag_lo, mag_hi, mag_label in [(-1.5, -0.5, 'gap -.5 to -1.5'),
                                           (-99, -1.5, 'gap -1.5+')]:
            sub = df[(df['models_under'] == 3) &
                     (df['signal_confluence_net'] >= snc_lo) &
                     (df['signal_confluence_net'] < snc_hi) &
                     (df['gap_composite'] >= mag_lo) &
                     (df['gap_composite'] < mag_hi)]
            n = len(sub)
            if n < 5: continue
            hits = int((sub['label_over'] == 0).sum())
            print(f'  all3 + {snc_label} + {mag_label}: n={n}, UNDER {hits}/{n} ({100*hits/n:.1f}%)')


if __name__ == '__main__':
    main()
