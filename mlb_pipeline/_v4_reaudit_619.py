"""Re-audit v4 vs proj vs jerry on totals over 7/14/30/60/90d windows.
Break down OVER vs UNDER separately to confirm/refute the OVER-drift
saved in project_v4_over_drift memory."""
import os, requests, json
from collections import defaultdict
import statistics as st
from datetime import date, timedelta
from dotenv import load_dotenv
load_dotenv()
SU = os.environ.get('SUPABASE_URL'); SK = os.environ.get('SUPABASE_KEY')
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

# Pull last 120 days of resolved games
today = date(2026, 6, 19)
since = (today - timedelta(days=120)).isoformat()

r = requests.get(f'{SU}/rest/v1/mlb_game_results',
    params={'game_date': f'gte.{since}',
            'select': 'game_date,close_total,projected_total,model_pred_total,jerry_pred_total,close_spread,projected_spread,model_pred_spread,jerry_pred_spread,home_score,away_score',
            'order': 'game_date.asc'},
    headers=H, timeout=20)
games = [g for g in r.json() if g.get('home_score') is not None and g.get('away_score') is not None]
print(f'Pulled {len(games)} resolved games since {since}')


def in_window(gd, days):
    try:
        d = date.fromisoformat(gd)
    except (TypeError, ValueError):
        return False
    return (today - d).days <= days


def grade_direction(pred, line, actual):
    """Return 'O','U','P', or None"""
    if pred is None or line is None or actual is None:
        return None
    if actual == line: return 'P'
    pred_dir = 'O' if pred > line else 'U'
    actual_dir = 'O' if actual > line else 'U'
    return pred_dir if pred_dir == actual_dir else f'!{pred_dir}'  # ! = miss


def grade_spread(pred, actual_home_won):
    """Spread direction grade. Returns 'W' / 'L' / None"""
    if pred is None: return None
    pred_home = float(pred) > 0
    return 'W' if pred_home == actual_home_won else 'L'


# ──────────────── TOTALS by direction × model × window ────────────────
print()
print('='*100)
print('TOTALS — direction accuracy by model × window × OVER/UNDER predicted')
print('='*100)

WINDOWS = [('7d',7), ('14d',14), ('30d',30), ('60d',60), ('90d',90)]
MODELS = [('proj','projected_total'), ('jerry','jerry_pred_total'), ('v4','model_pred_total')]

for wlabel, wdays in WINDOWS:
    wgames = [g for g in games if in_window(g['game_date'], wdays)]
    print(f'\n--- {wlabel} (n={len(wgames)}) ---')
    print(f'  {"model":>6s} | {"all":>15s} | {"pred OVER":>15s} | {"pred UNDER":>15s} | {"mean err":>8s}')
    for mlabel, mfield in MODELS:
        all_w=all_l=ovr_w=ovr_l=undr_w=undr_l=0
        errs=[]
        for g in wgames:
            line = g.get('close_total'); pred = g.get(mfield)
            actual = g['home_score']+g['away_score']
            if pred is None or line is None: continue
            errs.append(pred - actual)
            if actual == line: continue
            pred_dir = 'O' if pred > line else 'U'
            actual_dir = 'O' if actual > line else 'U'
            if pred_dir == actual_dir:
                all_w += 1
                if pred_dir == 'O': ovr_w += 1
                else: undr_w += 1
            else:
                all_l += 1
                if pred_dir == 'O': ovr_l += 1
                else: undr_l += 1
        all_n = all_w + all_l
        ovr_n = ovr_w + ovr_l
        undr_n = undr_w + undr_l
        me = f'{st.mean(errs):+.2f}' if errs else '-'
        print(f'  {mlabel:>6s} | {all_w:>3}-{all_l:<3} {100*all_w/max(1,all_n):>3.0f}% (n={all_n:<3}) | '
              f'{ovr_w:>3}-{ovr_l:<3} {100*ovr_w/max(1,ovr_n):>3.0f}% (n={ovr_n:<3}) | '
              f'{undr_w:>3}-{undr_l:<3} {100*undr_w/max(1,undr_n):>3.0f}% (n={undr_n:<3}) | {me:>8s}')

# ──────────────── SPREAD direction (predicts ML winner) ────────────────
print()
print('='*100)
print('SPREAD direction — predicts ML winner — by model × window')
print('='*100)
SP_MODELS = [('proj','projected_spread'), ('jerry','jerry_pred_spread'), ('v4','model_pred_spread')]
for wlabel, wdays in WINDOWS:
    wgames = [g for g in games if in_window(g['game_date'], wdays)]
    print(f'\n--- {wlabel} (n={len(wgames)}) ---')
    for mlabel, mfield in SP_MODELS:
        w=l=0
        for g in wgames:
            pred = g.get(mfield)
            actual_home = g['home_score'] > g['away_score']
            if pred is None: continue
            pred_home = float(pred) > 0
            if pred_home == actual_home: w += 1
            else: l += 1
        n = w + l
        print(f'  {mlabel:>6s}: {w}-{l} ({100*w/max(1,n):.0f}%, n={n})')

# ──────────────── CONSENSUS COHORTS — what if we drop v4 / use 2-of-3? ─────
print()
print('='*100)
print('CONSENSUS METHOD AUDIT — direction accuracy of different vote rules (30d)')
print('='*100)
wgames = [g for g in games if in_window(g['game_date'], 30)]
print(f'  n={len(wgames)} games')

def vote_dir(p, line):
    if p is None or line is None: return None
    if p == line: return 'P'
    return 'O' if p > line else 'U'

methods = {
    'proj alone':                lambda g: vote_dir(g.get('projected_total'), g['close_total']),
    'jerry alone':               lambda g: vote_dir(g.get('jerry_pred_total'), g['close_total']),
    'v4 alone':                  lambda g: vote_dir(g.get('model_pred_total'), g['close_total']),
    'proj+jerry majority':       lambda g: _majority([vote_dir(g.get('projected_total'), g['close_total']),
                                                       vote_dir(g.get('jerry_pred_total'), g['close_total'])]),
    'proj+v4 majority':          lambda g: _majority([vote_dir(g.get('projected_total'), g['close_total']),
                                                       vote_dir(g.get('model_pred_total'), g['close_total'])]),
    'jerry+v4 majority':         lambda g: _majority([vote_dir(g.get('jerry_pred_total'), g['close_total']),
                                                       vote_dir(g.get('model_pred_total'), g['close_total'])]),
    'all 3 unanimous':           lambda g: _unanimous([vote_dir(g.get('projected_total'), g['close_total']),
                                                        vote_dir(g.get('jerry_pred_total'), g['close_total']),
                                                        vote_dir(g.get('model_pred_total'), g['close_total'])]),
    '2-of-3 majority':           lambda g: _majority([vote_dir(g.get('projected_total'), g['close_total']),
                                                       vote_dir(g.get('jerry_pred_total'), g['close_total']),
                                                       vote_dir(g.get('model_pred_total'), g['close_total'])]),
    'proj+jerry agree (drop v4)':lambda g: _agree_or_none([vote_dir(g.get('projected_total'), g['close_total']),
                                                              vote_dir(g.get('jerry_pred_total'), g['close_total'])]),
}

def _majority(votes):
    votes = [v for v in votes if v in ('O','U')]
    if not votes: return None
    o = votes.count('O'); u = votes.count('U')
    if o > u: return 'O'
    if u > o: return 'U'
    return None  # tie

def _unanimous(votes):
    if any(v not in ('O','U') for v in votes): return None
    if all(v == votes[0] for v in votes): return votes[0]
    return None

def _agree_or_none(votes):
    if all(v == votes[0] and v in ('O','U') for v in votes): return votes[0]
    return None

for mname, fn in methods.items():
    w=l=skipped=0
    for g in wgames:
        line = g.get('close_total')
        actual = g['home_score']+g['away_score']
        if line is None: skipped += 1; continue
        vote = fn(g)
        if vote not in ('O','U'): skipped += 1; continue
        actual_dir = 'O' if actual > line else ('U' if actual < line else None)
        if actual_dir is None: skipped += 1; continue
        if vote == actual_dir: w += 1
        else: l += 1
    n = w + l
    pct = f'{100*w/n:.0f}%' if n else '-'
    print(f'  {mname:30s}: {w:>3}-{l:<3} ({pct:>4s}, n={n}, skipped {skipped})')
