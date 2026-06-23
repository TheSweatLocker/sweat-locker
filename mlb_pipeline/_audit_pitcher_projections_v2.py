"""Audit pitcher projection accuracy — using signals JSON inside mlb_pipeline_props.

Each pitcher prop carries _projected_X in its signals dict (the system's
internal projection that drives the conviction). Compare that to final_value
(the actual result).
"""
import os
from collections import defaultdict
from datetime import date, timedelta
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

PROP_TO_METRIC = {
    'er_over': '_projected_er', 'er_under': '_projected_er',
    'ks_over': '_projected_ks', 'ks_under': '_projected_ks',
    'bb_over': '_projected_bb', 'bb_under': '_projected_bb',
    'ha_over': '_projected_hits', 'ha_under': '_projected_hits',
    'outs_over': '_projected_outs', 'outs_under': '_projected_outs',
}


def pull():
    rows = []
    off = 0
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_pipeline_props?result=in.(Win,Loss)'
            f'&final_value=not.is.null'
            f'&prop_type=in.(er_over,er_under,ks_over,ks_under,bb_over,bb_under,'
            f'ha_over,ha_under,outs_over,outs_under)'
            f'&select=game_date,player_name,prop_type,prop_line,conviction,tier,'
            f'final_value,signals'
            f'&limit=1000&offset={off}',
            headers=H, timeout=30,
        )
        chunk = r.json()
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


def main():
    print('Pulling pitcher prop history with projections in signals JSON...')
    rows = pull()
    print(f'  {len(rows)} graded pitcher props')

    # Extract projection from signals
    records = []
    for p in rows:
        if not isinstance(p, dict): continue
        sigs = p.get('signals') or {}
        if not isinstance(sigs, dict): continue
        metric_key = PROP_TO_METRIC.get(p['prop_type'])
        if not metric_key: continue
        proj = sigs.get(metric_key)
        if proj is None: continue
        try:
            proj_val = float(proj)
            actual = float(p['final_value'])
            line = float(p['prop_line'])
        except (ValueError, TypeError): continue

        # Direction-normalized: we want to know if projection said OVER or UNDER
        # vs line, and if actual matched
        proj_direction = 'OVER' if proj_val > line else ('UNDER' if proj_val < line else 'PUSH')
        actual_direction = 'OVER' if actual > line else ('UNDER' if actual < line else 'PUSH')
        records.append({
            'date': p['game_date'],
            'pitcher': p['player_name'],
            'prop_type': p['prop_type'],
            'metric': PROP_TO_METRIC[p['prop_type']].replace('_projected_', ''),
            'line': line,
            'projected': proj_val,
            'actual': actual,
            'proj_dir': proj_direction,
            'actual_dir': actual_direction,
            'err': proj_val - actual,
            'abs_err': abs(proj_val - actual),
            'tier': p['tier'],
            'conviction': p['conviction'],
        })
    df = pd.DataFrame(records)
    print(f'  {len(df)} records with valid projection + actual\n')

    if len(df) == 0:
        print('No records!')
        return

    # MAE per metric
    print('=' * 90)
    print('PROJECTION ACCURACY (internal _projected_X vs actual final_value)')
    print('=' * 90)
    print()
    print(f'{"Metric":>8s} | {"n":>6s} | {"MAE":>6s} | {"bias (proj-actual)":>20s} | {"over-proj rate":>16s} | {"dir-acc":>8s}')
    print('-' * 95)
    for metric in ['er', 'ks', 'bb', 'hits', 'outs']:
        sub = df[df['metric'] == metric]
        if len(sub) == 0: continue
        mae = sub['abs_err'].mean()
        bias = sub['err'].mean()
        over_rate = (sub['err'] > 0).mean() * 100
        dir_match = ((sub['proj_dir'] == sub['actual_dir']) & (sub['proj_dir'] != 'PUSH')).sum()
        dir_total = (sub['proj_dir'] != 'PUSH').sum()
        dir_acc = 100*dir_match/max(1,dir_total)
        print(f'  {metric:>6s} | {len(sub):>6d} | {mae:>5.2f} | {bias:>+18.2f} | {over_rate:>14.0f}% | {dir_acc:>6.0f}%')

    # ER calibration band
    print()
    print('=== ER calibration by projection band ===')
    er = df[df['metric'] == 'er']
    if len(er) > 0:
        for lo, hi, label in [(0, 1.5, '< 1.5 (panel says elite)'),
                               (1.5, 2.5, '1.5-2.5 (solid)'),
                               (2.5, 3.5, '2.5-3.5 (avg)'),
                               (3.5, 99, '3.5+ (panel says poor)')]:
            sub = er[(er['projected'] >= lo) & (er['projected'] < hi)]
            if len(sub) == 0: continue
            avg_actual = sub['actual'].mean()
            avg_proj = sub['projected'].mean()
            print(f'  Projected {label:>26s} (n={len(sub):>3d}): actual avg {avg_actual:.2f}, proj avg {avg_proj:.2f}, diff {avg_proj - avg_actual:+.2f}')

    # Big-miss audit on ER
    print()
    print('=== ER — big misses (proj off by ≥ 2.0 ER) ===')
    er_big = er[er['abs_err'] >= 2.0]
    print(f'  {len(er_big)} of {len(er)} ER projections off by ≥2 ({100*len(er_big)/max(1,len(er)):.0f}%)')
    if len(er_big) > 0:
        over = (er_big['err'] > 0).sum()
        under = (er_big['err'] < 0).sum()
        print(f'    Over-projected (predicted high, actual low):  {over} ({100*over/len(er_big):.0f}%)')
        print(f'    Under-projected (predicted low, actual high): {under} ({100*under/len(er_big):.0f}%)')

    # Compare to "math" approach
    # For each prop, if we computed math as some formula, would it be better?
    # We need xERA / L3 ERA for this — pull from mlb_game_results via pitcher name + date
    print()
    print('=== Hit rate of PRIME tier projections (when system was confident) ===')
    print()
    print(f'{"Metric":>8s} | {"PRIME hit rate":>16s} | {"STRONG":>14s} | {"LEAN":>14s}')
    for metric in ['er', 'ks', 'bb', 'hits', 'outs']:
        for tier in ['PRIME', 'STRONG', 'LEAN']:
            sub = df[(df['metric'] == metric) & (df['tier'] == tier)]
            if len(sub) == 0: continue
            dir_match = (sub['proj_dir'] == sub['actual_dir']).sum()
            # use line direction
        # combined row
        prime = df[(df['metric'] == metric) & (df['tier'] == 'PRIME')]
        strong = df[(df['metric'] == metric) & (df['tier'] == 'STRONG')]
        lean = df[(df['metric'] == metric) & (df['tier'] == 'LEAN')]
        def hit_rate(sub):
            if len(sub) == 0: return '-'
            hits = (sub['proj_dir'] == sub['actual_dir']).sum()
            return f'{hits}/{len(sub)} ({100*hits/len(sub):.0f}%)'
        print(f'  {metric:>6s} | {hit_rate(prime):>16s} | {hit_rate(strong):>14s} | {hit_rate(lean):>14s}')

    # Recency comparison
    print()
    print('=== ER MAE by recency (is calibration drifting?) ===')
    df['date'] = pd.to_datetime(df['date'])
    today = pd.Timestamp(date.today())
    def bucket(d):
        days_ago = (today - d).days
        if days_ago <= 7: return '1-7d'
        if days_ago <= 14: return '8-14d'
        if days_ago <= 30: return '15-30d'
        if days_ago <= 60: return '31-60d'
        return '>60d'
    df['window'] = df['date'].apply(bucket)
    er = df[df['metric'] == 'er']
    for w in ['1-7d', '8-14d', '15-30d', '31-60d', '>60d']:
        sub = er[er['window'] == w]
        if len(sub) == 0: continue
        print(f'  {w:>6s}: n={len(sub):>4d}, MAE {sub["abs_err"].mean():.2f}, bias {sub["err"].mean():+.2f}')


if __name__ == '__main__':
    main()
