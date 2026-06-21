"""Deep pattern mining — every cross-cut we can think of.

Builds on _audit_cross_model_patterns.py. The first audit found loud
4-way cohorts. This one slices the same data on:
  - Line band (low/mid/high total)
  - Park factor (pitcher/neutral/hitter)
  - Temperature
  - Bullpen disparity
  - Pitcher form delta (L3 vs season)
  - Day-of-week
  - Cohort engine net direction

Goal: find n>=20 cohorts with |edge|>=10pt that we can codify into
sweat-dim rules. Anything that passes the gate gets shipped.
"""
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from v5_inference import predict_ml, predict_total

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

MIN_N = 20
MIN_EDGE = 0.10  # 10pp from coinflip


def pull(window_days=90):
    since = (date.today() - timedelta(days=window_days)).isoformat()
    cols = ('game_date,home_score,away_score,close_total,close_spread,'
            'projected_total,model_pred_total,jerry_pred_total,'
            'projected_spread,model_pred_spread,jerry_pred_spread,'
            'signal_confluence_net,nrfi_score,'
            'away_sp_xera,home_sp_xera,away_pitcher_last_3_era,home_pitcher_last_3_era,'
            'away_bullpen_era,home_bullpen_era,away_wrc_plus,home_wrc_plus,'
            'away_ops_last7,home_ops_last7,away_team_k_pct,home_team_k_pct,'
            'park_run_factor,temperature,'
            'home_ml_close,away_ml_close')
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?game_date=gte.{since}'
            f'&select={cols}&order=game_date.asc&limit=1000&offset={offset}',
            headers=H, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return [r for r in rows if r.get('home_score') is not None]


def total_dir(p, line, eps=0.3):
    if p is None or line is None: return None
    try:
        pf = float(p); lf = float(line)
    except (TypeError, ValueError):
        return None
    if pf > lf + eps: return 'O'
    if pf < lf - eps: return 'U'
    return None


def sp_pick(p):
    if p is None: return None
    try: v = float(p)
    except (TypeError, ValueError): return None
    if v < 0: return 'H'
    if v > 0: return 'A'
    return None


def fl(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def main():
    rows = pull()
    print(f'pulled {len(rows)} graded games (last 90d)')

    enriched = []
    for r in rows:
        line = r.get('close_total')
        actual = r['home_score'] + r['away_score']
        if line is None: continue
        e = dict(r)
        e['v3_t'] = total_dir(r.get('projected_total'), line)
        e['v4_t'] = total_dir(r.get('model_pred_total'), line)
        e['jr_t'] = total_dir(r.get('jerry_pred_total'), line)
        p_tot = predict_total(r)
        e['v5_t'] = 'O' if (p_tot is not None and p_tot >= 0.5) else ('U' if p_tot is not None else None)
        e['p_tot'] = p_tot
        e['v3_m'] = sp_pick(r.get('projected_spread'))
        e['v4_m'] = sp_pick(r.get('model_pred_spread'))
        e['jr_m'] = sp_pick(r.get('jerry_pred_spread'))
        p_ml = predict_ml(r)
        e['v5_m'] = 'H' if (p_ml is not None and p_ml >= 0.5) else ('A' if p_ml is not None else None)
        e['p_ml'] = p_ml
        e['actual_t'] = 'O' if actual > line else ('U' if actual < line else None)
        e['actual_m'] = 'H' if r['home_score'] > r['away_score'] else 'A'
        # derived features
        e['line_band'] = ('low' if line <= 7.5 else 'mid' if line <= 9.5 else 'high')
        prf = fl(r.get('park_run_factor'))
        e['park_band'] = ('pitcher' if prf and prf <= 95 else 'neutral' if prf and prf <= 105 else 'hitter') if prf else 'unknown'
        temp = fl(r.get('temperature'))
        e['temp_band'] = ('cold' if temp and temp < 65 else 'warm' if temp and temp <= 80 else 'hot') if temp else 'unknown'
        bp_a = fl(r.get('away_bullpen_era')); bp_h = fl(r.get('home_bullpen_era'))
        if bp_a is not None and bp_h is not None:
            e['bp_max'] = max(bp_a, bp_h)
            e['bp_band'] = ('elite' if max(bp_a, bp_h) <= 3.5 else 'mid' if max(bp_a, bp_h) <= 4.3 else 'shaky')
        else:
            e['bp_band'] = 'unknown'
        snc = fl(r.get('signal_confluence_net')) or 0
        e['snc_band'] = ('loud_over' if snc >= 4 else 'loud_under' if snc <= -4 else
                          'mid_over' if snc >= 2 else 'mid_under' if snc <= -2 else 'neutral')
        enriched.append(e)

    print(f'enriched: {len(enriched)} games\n')

    # === CROSS-MODEL × LINE BAND ===
    print('=' * 100)
    print('CROSS-MODEL × LINE BAND (totals only — when does v3+v4+jerry+v5 unanimous OVER hit?)')
    print('=' * 100)
    found = []
    for band in ['low', 'mid', 'high']:
        subset = [e for e in enriched if e.get('line_band') == band and e.get('actual_t')]
        # 4-way unanimous OVER
        cohort = [e for e in subset if e['v3_t'] == 'O' and e['v4_t'] == 'O' and e['jr_t'] == 'O' and e['v5_t'] == 'O']
        if len(cohort) >= MIN_N:
            w = sum(1 for e in cohort if e['actual_t'] == 'O')
            rate = w / len(cohort)
            if abs(rate - 0.5) >= MIN_EDGE:
                found.append(('TOT 4-way OVER', band, w, len(cohort)-w, len(cohort), rate))
        # 4-way unanimous UNDER
        cohort = [e for e in subset if e['v3_t'] == 'U' and e['v4_t'] == 'U' and e['jr_t'] == 'U' and e['v5_t'] == 'U']
        if len(cohort) >= MIN_N:
            w = sum(1 for e in cohort if e['actual_t'] == 'U')
            rate = w / len(cohort)
            if abs(rate - 0.5) >= MIN_EDGE:
                found.append(('TOT 4-way UNDER', band, w, len(cohort)-w, len(cohort), rate))
    for label, band, w, l, n, rate in found:
        print(f'  {label} × {band:>4s} line: {w}-{l} ({rate*100:.0f}%, n={n})')
    if not found:
        print('  (no significant)')

    # === v5 DISSENT from v3+v4 (the loud one from earlier audit) ===
    print()
    print('=' * 100)
    print('v5 DISSENT from v3+v4 CONSENSUS (the headline pattern from earlier audit)')
    print('=' * 100)
    # Build the pattern across all sub-contexts: v3+v4 agree, v5 dissents
    for target_key, label in [('actual_t', 'TOTAL'), ('actual_m', 'ML')]:
        v3k = 'v3_t' if label == 'TOTAL' else 'v3_m'
        v4k = 'v4_t' if label == 'TOTAL' else 'v4_m'
        v5k = 'v5_t' if label == 'TOTAL' else 'v5_m'
        valids = ('O','U') if label == 'TOTAL' else ('H','A')
        # When v3+v4 agree AND v5 dissents — what happens to the v3+v4 pick?
        v34_pick_wins = v34_pick_losses = 0
        v34_pick_wins_no_dis = v34_pick_losses_no_dis = 0
        for e in enriched:
            if e[target_key] is None: continue
            v3v = e.get(v3k); v4v = e.get(v4k); v5v = e.get(v5k)
            if v3v not in valids or v4v not in valids or v5v not in valids: continue
            if v3v != v4v: continue  # need v3+v4 agreement
            pick = v3v
            if v5v != pick:  # v5 dissents
                if pick == e[target_key]: v34_pick_wins += 1
                else: v34_pick_losses += 1
            else:  # v5 agrees
                if pick == e[target_key]: v34_pick_wins_no_dis += 1
                else: v34_pick_losses_no_dis += 1
        nd = v34_pick_wins + v34_pick_losses
        na = v34_pick_wins_no_dis + v34_pick_losses_no_dis
        print(f'  [{label}] v3+v4 agree, v5 AGREES:    pick wins {v34_pick_wins_no_dis}-{v34_pick_losses_no_dis} ({100*v34_pick_wins_no_dis/max(1,na):.0f}%, n={na})')
        print(f'  [{label}] v3+v4 agree, v5 DISSENTS:  pick wins {v34_pick_wins}-{v34_pick_losses} ({100*v34_pick_wins/max(1,nd):.0f}%, n={nd}) ← FADE signal if low')

    # === LOUD COHORT NET vs MODEL AGREEMENT ===
    print()
    print('=' * 100)
    print('COHORT ENGINE × MODEL AGREEMENT (does loud confluence help when models agree?)')
    print('=' * 100)
    # For totals: when cohort_net loud OVER + ALL models agree OVER vs cohort loud + models split
    for snc_band, snc_dir in [('loud_over', 'O'), ('loud_under', 'U')]:
        subset = [e for e in enriched if e.get('snc_band') == snc_band and e['actual_t']]
        # Full alignment
        aligned = [e for e in subset if e['v3_t'] == snc_dir and e['v4_t'] == snc_dir and e['v5_t'] == snc_dir]
        if len(aligned) >= MIN_N:
            w = sum(1 for e in aligned if e['actual_t'] == snc_dir)
            print(f'  loud cohort ({snc_band}) + v3+v4+v5 all {snc_dir}: {w}-{len(aligned)-w} ({100*w/len(aligned):.0f}%, n={len(aligned)})')
        # Cohort loud but at least one model dissents
        contested = [e for e in subset if e['v3_t'] != snc_dir or e['v4_t'] != snc_dir or e['v5_t'] != snc_dir]
        if len(contested) >= MIN_N:
            # Pick cohort direction anyway
            w = sum(1 for e in contested if e['actual_t'] == snc_dir)
            print(f'  loud cohort ({snc_band}) but model dissent: picking cohort dir: {w}-{len(contested)-w} ({100*w/len(contested):.0f}%, n={len(contested)})')

    # === LINE BAND × MODEL AGREEMENT (high-line specific) ===
    print()
    print('=' * 100)
    print('HIGH-LINE games (close_total > 9.5) × model agreement')
    print('=' * 100)
    high = [e for e in enriched if e.get('line_band') == 'high' and e['actual_t']]
    _base_u = sum(1 for e in high if e['actual_t'] == 'U') * 100 / max(1, len(high))
    print(f'  high-line games: n={len(high)}, base UNDER rate: {_base_u:.0f}%')
    for combo_name, combo_fn in [
        ('v3+v4+jerry+v5 all UNDER', lambda e: e['v3_t']==e['v4_t']==e['jr_t']==e['v5_t']=='U'),
        ('v5 UNDER, v3+v4 OVER', lambda e: e['v5_t']=='U' and e['v3_t']=='O' and e['v4_t']=='O'),
        ('v5 OVER (any others)', lambda e: e['v5_t']=='O'),
    ]:
        c = [e for e in high if combo_fn(e)]
        if len(c) >= 10:
            target = 'U' if 'UNDER' in combo_name else 'O' if 'OVER' in combo_name else None
            if target:
                w = sum(1 for e in c if e['actual_t'] == target)
                print(f'  {combo_name}: pick {target}: {w}-{len(c)-w} ({100*w/len(c):.0f}%, n={len(c)})')

    # === PARK FACTOR × MODEL ===
    print()
    print('=' * 100)
    print('PARK FACTOR × MODEL AGREEMENT')
    print('=' * 100)
    for park in ['pitcher', 'neutral', 'hitter']:
        subset = [e for e in enriched if e.get('park_band') == park and e['actual_t']]
        # v5 strong UNDER pick
        c = [e for e in subset if e['v5_t'] == 'U' and e['p_tot'] is not None and e['p_tot'] <= 0.40]
        if len(c) >= 15:
            w = sum(1 for e in c if e['actual_t'] == 'U')
            print(f'  park={park}, v5 confidence UNDER (p<=.40): {w}-{len(c)-w} ({100*w/len(c):.0f}%, n={len(c)})')
        # v5 strong OVER pick
        c = [e for e in subset if e['v5_t'] == 'O' and e['p_tot'] is not None and e['p_tot'] >= 0.60]
        if len(c) >= 15:
            w = sum(1 for e in c if e['actual_t'] == 'O')
            print(f'  park={park}, v5 confidence OVER (p>=.60):  {w}-{len(c)-w} ({100*w/len(c):.0f}%, n={len(c)})')

    # === BULLPEN BAND × MODEL ===
    print()
    print('=' * 100)
    print('BULLPEN BAND × MODEL AGREEMENT')
    print('=' * 100)
    for bp in ['elite', 'mid', 'shaky']:
        subset = [e for e in enriched if e.get('bp_band') == bp and e['actual_t']]
        c = [e for e in subset if e['v5_t'] == 'U' and e['p_tot'] is not None and e['p_tot'] <= 0.45]
        if len(c) >= 15:
            w = sum(1 for e in c if e['actual_t'] == 'U')
            print(f'  bp={bp}, v5 UNDER lean (p<=.45): {w}-{len(c)-w} ({100*w/len(c):.0f}%, n={len(c)})')
        c = [e for e in subset if e['v5_t'] == 'O' and e['p_tot'] is not None and e['p_tot'] >= 0.55]
        if len(c) >= 15:
            w = sum(1 for e in c if e['actual_t'] == 'O')
            print(f'  bp={bp}, v5 OVER lean (p>=.55):  {w}-{len(c)-w} ({100*w/len(c):.0f}%, n={len(c)})')

    # === HOME ML PRICE × MODEL ===
    print()
    print('=' * 100)
    print('HOME ML PRICE × MODEL (dog scenarios)')
    print('=' * 100)
    # Home dog cohort
    home_dogs = [e for e in enriched if (fl(e.get('home_ml_close')) or 0) > 0 and e.get('actual_m')]
    _base_h = sum(1 for e in home_dogs if e['actual_m'] == 'H') * 100 / max(1, len(home_dogs))
    print(f'  home dog universe: n={len(home_dogs)}, base home win rate: {_base_h:.0f}%')
    # v5 says HOME (dog) wins
    c = [e for e in home_dogs if e['v5_m'] == 'H' and e['p_ml'] is not None and e['p_ml'] >= 0.55]
    if len(c) >= 15:
        w = sum(1 for e in c if e['actual_m'] == 'H')
        print(f'  home dog + v5 HOME lean (p>=.55): {w}-{len(c)-w} ({100*w/len(c):.0f}%, n={len(c)})')
    # When v5 fades home dog
    c = [e for e in home_dogs if e['v5_m'] == 'A' and e['p_ml'] is not None and e['p_ml'] <= 0.45]
    if len(c) >= 15:
        w = sum(1 for e in c if e['actual_m'] == 'A')
        print(f'  home dog + v5 says AWAY favorite (p<=.45): {w}-{len(c)-w} ({100*w/len(c):.0f}%, n={len(c)})')


if __name__ == '__main__':
    main()
