"""Audit how accurate the Numbers Panel pitcher projections have actually been.

For each graded game in mlb_game_results, compare:
  - pitcher_projected_er (what Numbers Panel showed)  vs  actual ER (from props)
  - pitcher_projected_ks                              vs  actual Ks
  - pitcher_projected_bb                              vs  actual BB
  - pitcher_projected_hits                            vs  actual Hits
  - pitcher_projected_outs                            vs  actual Outs

Source of truth for actuals: mlb_pipeline_props.final_value (graded prop values).

Reports MAE (mean absolute error) by metric, per-band hit rate, and direction
(over-projects vs under-projects). Also compares to L3-blend "math" approach
to see if my method actually improves on what's already there.
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


def pull_projections():
    rows = []
    off = 0
    sel = ('game_date,home_team,away_team,home_sp_name,away_sp_name,'
           'home_pitcher_projected_er,away_pitcher_projected_er,'
           'home_pitcher_projected_ks,away_pitcher_projected_ks,'
           'home_pitcher_projected_bb,away_pitcher_projected_bb,'
           'home_pitcher_projected_hits,away_pitcher_projected_hits,'
           'home_pitcher_projected_outs,away_pitcher_projected_outs,'
           'home_sp_xera,away_sp_xera,'
           'home_pitcher_last_3_era,away_pitcher_last_3_era')
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?away_pitcher_projected_er=not.is.null'
            f'&select={sel}&order=game_date.desc&limit=1000&offset={off}',
            headers=H, timeout=30,
        )
        chunk = r.json()
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


def pull_actuals():
    """Pull prop results with final_value as actuals."""
    rows = []
    off = 0
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_pipeline_props?result=in.(Win,Loss)'
            f'&final_value=not.is.null'
            f'&prop_type=in.(er_over,er_under,ks_over,ks_under,bb_over,bb_under,'
            f'ha_over,ha_under,outs_over,outs_under,hits_over,hits_under)'
            f'&select=game_date,player_name,prop_type,final_value'
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
    print('Pulling Numbers Panel projections...')
    proj_rows = pull_projections()
    print(f'  {len(proj_rows)} graded games with projections')

    print('Pulling prop actuals...')
    actuals = pull_actuals()
    print(f'  {len(actuals)} graded props with final values')

    # Index actuals by (date, pitcher_name, metric)
    # er_over and er_under share the same final_value (actual ER)
    metric_map = {
        'er_over': 'er', 'er_under': 'er',
        'ks_over': 'ks', 'ks_under': 'ks',
        'bb_over': 'bb', 'bb_under': 'bb',
        'ha_over': 'ha', 'ha_under': 'ha',  # hits allowed by pitcher
        'outs_over': 'outs', 'outs_under': 'outs',
        'hits_over': 'hits', 'hits_under': 'hits',  # batter hits
    }
    actual_idx = {}
    for a in actuals:
        if not isinstance(a, dict): continue
        key = (a['game_date'], a['player_name'], metric_map.get(a['prop_type']))
        if key[2] is None: continue
        if key not in actual_idx:
            actual_idx[key] = a['final_value']

    print(f'  {len(actual_idx)} unique (date, pitcher, metric) entries')

    # Now compare projections to actuals
    comparisons = []
    for g in proj_rows:
        if not isinstance(g, dict): continue
        date = g['game_date']
        for side in ('home', 'away'):
            pitcher = g.get(f'{side}_sp_name')
            if not pitcher: continue
            for metric, panel_field in [
                ('er', f'{side}_pitcher_projected_er'),
                ('ks', f'{side}_pitcher_projected_ks'),
                ('bb', f'{side}_pitcher_projected_bb'),
                ('ha', f'{side}_pitcher_projected_hits'),
                ('outs', f'{side}_pitcher_projected_outs'),
            ]:
                projected = g.get(panel_field)
                actual = actual_idx.get((date, pitcher, metric))
                if projected is None or actual is None: continue
                try:
                    p = float(projected); a = float(actual)
                    comparisons.append({
                        'date': date, 'pitcher': pitcher, 'metric': metric,
                        'projected': p, 'actual': a, 'err': p - a, 'abs_err': abs(p - a),
                        'xera': g.get(f'{side}_sp_xera'),
                        'l3_era': g.get(f'{side}_pitcher_last_3_era'),
                    })
                except (ValueError, TypeError):
                    continue

    df = pd.DataFrame(comparisons)
    print(f'\nTotal projection-vs-actual pairs: {len(df)}')

    print()
    print('=' * 80)
    print('NUMBERS PANEL PROJECTION ACCURACY (across all graded games)')
    print('=' * 80)
    print()
    print(f'{"Metric":>8s} | {"n":>6s} | {"MAE":>6s} | {"bias":>8s} | {"over-proj rate":>16s}')
    print('-' * 70)
    for metric in ['er', 'ks', 'bb', 'ha', 'outs']:
        sub = df[df['metric'] == metric]
        if len(sub) == 0: continue
        mae = sub['abs_err'].mean()
        bias = sub['err'].mean()
        over_rate = (sub['err'] > 0).mean() * 100
        print(f'  {metric:>6s} | {len(sub):>6d} | {mae:>5.2f} | {bias:>+7.2f} | {over_rate:>13.0f}%')

    print()
    print('=== ER specifically — calibration by projection band ===')
    er = df[df['metric'] == 'er'].copy()
    if len(er):
        for lo, hi, label in [(0, 1.5, '< 1.5 (elite)'),
                               (1.5, 2.5, '1.5-2.5 (solid)'),
                               (2.5, 3.5, '2.5-3.5 (avg)'),
                               (3.5, 99, '3.5+ (poor)')]:
            sub = er[(er['projected'] >= lo) & (er['projected'] < hi)]
            if len(sub) == 0: continue
            print(f'  Panel said {label:>18s}: n={len(sub):>4d}, actual avg ER {sub["actual"].mean():.2f}, panel said avg {sub["projected"].mean():.2f}')

    print()
    print('=== ER — where Panel WAS WRONG by ≥2 (the big-miss cases) ===')
    er_big = er[er['abs_err'] >= 2.0].copy()
    print(f'  {len(er_big)} cases (out of {len(er)} = {100*len(er_big)/max(1,len(er)):.0f}%) where Panel was off by 2+ ER')
    if len(er_big):
        # Direction of big misses
        over = (er_big['err'] > 0).sum()
        under = (er_big['err'] < 0).sum()
        print(f'    Panel OVER-projected by 2+: {over} times ({100*over/len(er_big):.0f}%)')
        print(f'    Panel UNDER-projected by 2+: {under} times ({100*under/len(er_big):.0f}%)')

    print()
    print('=== Compare to "math" approach (50/50 xERA + L3 over 4.5 IP) ===')
    er_math = er.copy()
    er_math['xera'] = pd.to_numeric(er_math['xera'], errors='coerce')
    er_math['l3_era'] = pd.to_numeric(er_math['l3_era'], errors='coerce')
    er_math = er_math.dropna(subset=['xera', 'l3_era'])
    er_math['math_proj'] = (er_math['xera'] * 0.5 + er_math['l3_era'] * 0.5) * 4.5 / 9
    er_math['math_err'] = er_math['math_proj'] - er_math['actual']
    er_math['math_abs_err'] = er_math['math_err'].abs()
    panel_mae = er_math['abs_err'].mean()
    math_mae = er_math['math_abs_err'].mean()
    print(f'  n={len(er_math)}  Panel MAE {panel_mae:.2f}  Math MAE {math_mae:.2f}  diff {math_mae - panel_mae:+.2f}')

    # Where do they disagree most?
    er_math['diff'] = (er_math['math_proj'] - er_math['projected']).abs()
    biggest = er_math.nlargest(10, 'diff')
    print(f'\n  Top 10 Panel-vs-math disagreements:')
    for _, r in biggest.iterrows():
        winner = 'PANEL' if abs(r['err']) < abs(r['math_err']) else 'MATH'
        print(f'    {r["date"]} {r["pitcher"][:18]:>18s}: Panel {r["projected"]:.1f}, Math {r["math_proj"]:.1f}, Actual {r["actual"]:.1f}  → {winner} closer')


if __name__ == '__main__':
    main()
