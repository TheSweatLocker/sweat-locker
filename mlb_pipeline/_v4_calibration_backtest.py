"""Deeper v4 backtest: find calibration offset + conditional accuracy.

Goal:
  1. Compute optimal v4 OVER-bias correction (the +0.5 average overshoot)
  2. Identify CONDITIONS where v4 is accurate vs broken
  3. Backtest a conditional + calibrated v4 vs raw v4 vs drop-v4
"""
import os, requests, json
from collections import defaultdict
import statistics as st
from datetime import date, timedelta
from dotenv import load_dotenv
load_dotenv()
SU = os.environ.get('SUPABASE_URL'); SK = os.environ.get('SUPABASE_KEY')
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

today = date(2026, 6, 19)
since = (today - timedelta(days=120)).isoformat()
r = requests.get(f'{SU}/rest/v1/mlb_game_results',
    params={'game_date': f'gte.{since}',
            'select': 'game_date,close_total,projected_total,model_pred_total,jerry_pred_total,park_run_factor,temperature,wind_mph,is_dome,home_score,away_score,home_sp_xera,away_sp_xera',
            'order': 'game_date.asc'},
    headers=H, timeout=20)
games = [g for g in r.json() if g.get('home_score') is not None and g.get('away_score') is not None
         and g.get('model_pred_total') is not None and g.get('close_total') is not None]
print(f'Pulled {len(games)} v4-valid games')

for g in games:
    g['actual'] = g['home_score'] + g['away_score']
    g['v4_edge'] = float(g['model_pred_total']) - float(g['close_total'])
    g['v4_err'] = float(g['model_pred_total']) - g['actual']

def in_window(gd, days):
    try: d = date.fromisoformat(gd)
    except: return False
    return (today - d).days <= days


# ──────────────── 1. OPTIMAL BIAS CORRECTION ────────────────
print()
print('='*100)
print('1. OPTIMAL BIAS CORRECTION — does a flat offset save v4?')
print('='*100)
WINDOWS = [('7d',7),('14d',14),('30d',30),('60d',60),('90d',90)]
print(f'  {"window":>7s} | {"raw mean err":>14s} | {"raw dir":>9s} | {"-0.3 offset":>13s} | {"-0.5 offset":>13s} | {"-0.7 offset":>13s}')
for wlabel, wdays in WINDOWS:
    wg = [g for g in games if in_window(g['game_date'], wdays)]
    if not wg: continue
    errs = [g['v4_err'] for g in wg]
    raw_me = st.mean(errs)
    def dir_acc(offset):
        w=l=0
        for g in wg:
            adj = float(g['model_pred_total']) + offset
            line = float(g['close_total']); actual = g['actual']
            if actual == line: continue
            p_o = adj > line; a_o = actual > line
            if p_o == a_o: w += 1
            else: l += 1
        n = w + l
        return f'{w}-{l} {100*w/max(1,n):.0f}%'
    print(f'  {wlabel:>7s} | {raw_me:+13.2f}   | {dir_acc(0):>9s} | {dir_acc(-0.3):>13s} | {dir_acc(-0.5):>13s} | {dir_acc(-0.7):>13s}')


# ──────────────── 2. CONDITIONAL ACCURACY ────────────────
print()
print('='*100)
print('2. CONDITIONAL ACCURACY — where is v4 accurate vs broken (30d)?')
print('='*100)
wg = [g for g in games if in_window(g['game_date'], 30)]

def cond_acc(filter_fn, label):
    matches = [g for g in wg if filter_fn(g)]
    w=l=0
    for g in matches:
        line = float(g['close_total']); actual = g['actual']
        v4 = float(g['model_pred_total'])
        if actual == line: continue
        p_o = v4 > line; a_o = actual > line
        if p_o == a_o: w += 1
        else: l += 1
    n = w + l
    if n == 0:
        return None
    return f'{label:50s}: {w:>3}-{l:<3} ({100*w/n:>3.0f}%, n={n})'

print(f'  n={len(wg)} games (30d window)')
print()
print('  EDGE-MAGNITUDE bands (v4_edge = v4_pred - close_total):')
for lo, hi, label in [
    (None, -2.0, 'v4 edge <= -2.0 (strong UNDER call)'),
    (-2.0, -1.0, 'v4 edge -2.0 to -1.0'),
    (-1.0, -0.3, 'v4 edge -1.0 to -0.3'),
    (-0.3, 0.3, 'v4 edge -0.3 to +0.3 (neutral)'),
    (0.3, 1.0, 'v4 edge +0.3 to +1.0'),
    (1.0, 2.0, 'v4 edge +1.0 to +2.0'),
    (2.0, None, 'v4 edge >= +2.0 (strong OVER call)'),
]:
    def make_fn(lo=lo, hi=hi):
        def fn(g):
            e = g['v4_edge']
            if lo is not None and e < lo: return False
            if hi is not None and e >= hi: return False
            return True
        return fn
    res = cond_acc(make_fn(), label)
    if res: print('  ' + res)

print()
print('  PARK FACTOR bands:')
for lo, hi, label in [
    (None, 92, 'park <= 92 (pitcher park)'),
    (92, 98, 'park 92-98 (neutral-pitcher)'),
    (98, 102, 'park 98-102 (neutral)'),
    (102, 108, 'park 102-108 (hitter-lean)'),
    (108, None, 'park >= 108 (extreme hitter)'),
]:
    def make_fn(lo=lo, hi=hi):
        def fn(g):
            p = g.get('park_run_factor') or 100
            try: p = float(p)
            except: return False
            if lo is not None and p < lo: return False
            if hi is not None and p >= hi: return False
            return True
        return fn
    res = cond_acc(make_fn(), label)
    if res: print('  ' + res)

print()
print('  CLOSE TOTAL bands:')
for lo, hi, label in [
    (None, 7.5, 'close_total <= 7.5 (low)'),
    (7.5, 8.5, 'close_total 7.5-8.5'),
    (8.5, 9.5, 'close_total 8.5-9.5'),
    (9.5, 10.5, 'close_total 9.5-10.5'),
    (10.5, None, 'close_total >= 10.5 (high)'),
]:
    def make_fn(lo=lo, hi=hi):
        def fn(g):
            t = g.get('close_total')
            if t is None: return False
            t = float(t)
            if lo is not None and t < lo: return False
            if hi is not None and t >= hi: return False
            return True
        return fn
    res = cond_acc(make_fn(), label)
    if res: print('  ' + res)

print()
print('  TEMPERATURE bands:')
for lo, hi, label in [
    (None, 55, 'temp < 55 (cold)'),
    (55, 65, 'temp 55-65 (cool)'),
    (65, 75, 'temp 65-75'),
    (75, 85, 'temp 75-85 (warm)'),
    (85, None, 'temp >= 85 (hot)'),
]:
    def make_fn(lo=lo, hi=hi):
        def fn(g):
            t = g.get('temperature')
            if t is None: return False
            t = float(t)
            if lo is not None and t < lo: return False
            if hi is not None and t >= hi: return False
            return True
        return fn
    res = cond_acc(make_fn(), label)
    if res: print('  ' + res)

print()
print('  Dome vs outdoor:')
for is_dome_val, label in [(True, 'dome game'), (False, 'outdoor game')]:
    res = cond_acc(lambda g, v=is_dome_val: bool(g.get('is_dome')) == v, label)
    if res: print('  ' + res)

print()
print('  COMBINED xERA SUM (pitcher matchup):')
for lo, hi, label in [
    (None, 7.0, 'combined xERA < 7.0 (both elite)'),
    (7.0, 8.5, 'combined xERA 7.0-8.5'),
    (8.5, 10.0, 'combined xERA 8.5-10.0'),
    (10.0, None, 'combined xERA >= 10.0 (both shaky)'),
]:
    def make_fn(lo=lo, hi=hi):
        def fn(g):
            h = g.get('home_sp_xera'); a = g.get('away_sp_xera')
            if h is None or a is None: return False
            s = float(h) + float(a)
            if lo is not None and s < lo: return False
            if hi is not None and s >= hi: return False
            return True
        return fn
    res = cond_acc(make_fn(), label)
    if res: print('  ' + res)

# ──────────────── 3. PROPOSED METHOD BACKTEST ────────────────
print()
print('='*100)
print('3. PROPOSED METHOD BACKTEST (30d)')
print('='*100)
print(f'  n={len(wg)} games')

def method_grade(fn, label):
    w=l=skipped=0
    for g in wg:
        line = float(g['close_total']); actual = g['actual']
        vote = fn(g)  # 'O' / 'U' / None
        if vote not in ('O','U'): skipped += 1; continue
        if actual == line: skipped += 1; continue
        actual_dir = 'O' if actual > line else 'U'
        if vote == actual_dir: w += 1
        else: l += 1
    n = w + l
    pct = f'{100*w/max(1,n):.0f}%'
    print(f'  {label:55s}: {w:>3}-{l:<3} ({pct}, n={n}, skipped {skipped})')

def v3_dir(g):
    p = g.get('projected_total')
    if p is None: return None
    if p > float(g['close_total']) + 0.3: return 'O'
    if p < float(g['close_total']) - 0.3: return 'U'
    return None

def v4_dir(g, offset=0):
    p = g.get('model_pred_total')
    if p is None: return None
    p = float(p) + offset
    if p > float(g['close_total']) + 0.3: return 'O'
    if p < float(g['close_total']) - 0.3: return 'U'
    return None

def jerry_dir(g):
    p = g.get('jerry_pred_total')
    if p is None: return None
    if p > float(g['close_total']) + 0.3: return 'O'
    if p < float(g['close_total']) - 0.3: return 'U'
    return None

def majority(votes):
    votes = [v for v in votes if v in ('O','U')]
    if not votes: return None
    o = votes.count('O'); u = votes.count('U')
    if o > u: return 'O'
    if u > o: return 'U'
    return None

def agree_or_none(*votes):
    votes = [v for v in votes if v in ('O','U')]
    if len(votes) < 2: return None
    if all(v == votes[0] for v in votes): return votes[0]
    return None

method_grade(lambda g: majority([v3_dir(g), jerry_dir(g), v4_dir(g)]),                            'baseline: 2-of-3 majority (current STRONG gate)')
method_grade(lambda g: agree_or_none(v3_dir(g), jerry_dir(g), v4_dir(g)),                          'CURRENT ELITE: all 3 unanimous')
method_grade(lambda g: agree_or_none(v3_dir(g), jerry_dir(g)),                                      'PROPOSED A: drop v4, proj+jerry agree')
method_grade(lambda g: majority([v3_dir(g), jerry_dir(g)]) or v4_dir(g),                            'PROPOSED B: proj+jerry majority, v4 fallback')
method_grade(lambda g: agree_or_none(v3_dir(g), jerry_dir(g), v4_dir(g, offset=-0.5)),              'PROPOSED C: unanimous + v4 OVER -0.5 corrected')
method_grade(lambda g: agree_or_none(v3_dir(g), jerry_dir(g), v4_dir(g, offset=-0.3)),              'PROPOSED D: unanimous + v4 OVER -0.3 corrected')

# CONDITIONAL v4 only when conditions favor accuracy
def v4_dir_conditional(g):
    """Use v4 only when its edge is loud AND conditions are stable."""
    p = g.get('model_pred_total')
    line = float(g['close_total'])
    if p is None: return None
    edge = float(p) - line
    if abs(edge) < 1.0: return None  # too small to trust
    # Skip extreme parks where v4 has shown bias
    park = g.get('park_run_factor') or 100
    try: park = float(park)
    except: park = 100
    if park >= 108: return None  # extreme hitter park, v4 over-projects
    # Direction
    return 'O' if edge > 0 else 'U'

method_grade(lambda g: majority([v3_dir(g), jerry_dir(g), v4_dir_conditional(g)]),                  'PROPOSED E: 2-of-3 with conditional v4 (loud + non-extreme park)')
method_grade(lambda g: agree_or_none(v3_dir(g), jerry_dir(g), v4_dir_conditional(g)),               'PROPOSED F: unanimous with conditional v4')
