"""NRFI reweight backtest — pull every NRFI-relevant feature we collect and
try every reasonable cohort split to see if any combination clears 60%+
on n >= 30.

Features available per game (from mlb_game_results):
  - nrfi_score (system's current composite)
  - home_first_inning_era, away_first_inning_era
  - home_first_inning_avg, away_first_inning_avg
  - home_first_inning_bb, away_first_inning_bb
  - home_first_inning_hr, away_first_inning_hr
  - home_first_inning_ip, away_first_inning_ip (sample size guard)
  - home_first_inning_whip, away_first_inning_whip
  - home_sp_xera, away_sp_xera
  - home_sp_k_pct, away_sp_k_pct
  - home_team_k_pct, away_team_k_pct (opp K vulnerability)
  - home_pitcher_last_3_era, away_pitcher_last_3_era
  - park_run_factor (cold parks suppress first-inning runs)
  - temperature
  - umpire (we have ump K/over rate elsewhere, skip here)
  - signal_confluence_net
  - home_wrc_plus, away_wrc_plus (opp offense quality)
  - home_runs_per_game, away_runs_per_game (opp offense)

Output: cohort table sorted by hit rate desc, with sample size for trust.
"""
import os, json, sys, io, urllib.request
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv('.env'); load_dotenv('mlb_pipeline/.env')
URL=os.environ['SUPABASE_URL']; KEY=os.environ['SUPABASE_KEY']
H={'apikey':KEY,'Authorization':f'Bearer {KEY}'}

def get(p):
    req=urllib.request.Request(URL+p,headers={**H,'Range':'0-49999','Range-Unit':'items'})
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.loads(r.read())

print('Loading graded games with NRFI result populated...')
sel = ('game_date,home_team,away_team,nrfi_score,nrfi_result,home_score,away_score,'
       'home_first_inning_era,away_first_inning_era,'
       'home_first_inning_avg,away_first_inning_avg,'
       'home_first_inning_bb,away_first_inning_bb,'
       'home_first_inning_hr,away_first_inning_hr,'
       'home_first_inning_ip,away_first_inning_ip,'
       'home_first_inning_whip,away_first_inning_whip,'
       'home_sp_xera,away_sp_xera,'
       'home_sp_k_pct,away_sp_k_pct,'
       'home_team_k_pct,away_team_k_pct,'
       'home_pitcher_last_3_era,away_pitcher_last_3_era,'
       'park_run_factor,temperature,'
       'signal_confluence_net,'
       'home_wrc_plus,away_wrc_plus,'
       'home_runs_per_game,away_runs_per_game')
rows = get(f'/rest/v1/mlb_game_results?nrfi_result=not.is.null&nrfi_score=not.is.null&select={sel}&order=game_date.desc')
print(f'  {len(rows)} graded games with NRFI result + score')


def f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def report(name, w, l, min_n=10, price=-130):
    tot = w + l
    if tot < min_n:
        print(f'  {name:>55s}  n={tot:>4d}  (below min)')
        return None
    hit = w/tot*100
    if price > 0: payout = price/100
    else: payout = 100/abs(price)
    ev = (w * payout - l) / tot
    flag = ' ⭐' if hit >= 60 and ev > 0.05 else (' 🚫' if hit < 50 else '')
    print(f'  {name:>55s}  n={tot:>4d}  {w}-{l}  hit={hit:>5.1f}%  EV@{price}={ev:+.3f}{flag}')
    return (hit, ev, tot, w, l)


# =====================================================================
# A. Base NRFI score bands (current system)
# =====================================================================
print()
print('=' * 100)
print('A. CURRENT NRFI SCORE BANDS (current system, n=' + str(len(rows)) + ')')
print('=' * 100)

for lo, hi, label in [(95,100,'95+'),(90,94,'PRIME 90-94'),(85,89,'85-89'),(80,84,'80-84'),(70,79,'70-79'),(50,69,'50-69'),(0,49,'0-49 (YRFI side)')]:
    w = l = 0
    for r in rows:
        s = r.get('nrfi_score') or 0
        if not (lo <= s <= hi): continue
        result = r.get('nrfi_result')
        if result == 'NRFI': w += 1
        elif result == 'YRFI': l += 1
    report(f'NRFI {label}', w, l, min_n=10)


# =====================================================================
# B. First-inning ERA cohorts (independent of nrfi_score)
# =====================================================================
print()
print('=' * 100)
print('B. FIRST-INNING ERA — both starters ≤ X (NRFI angle)')
print('=' * 100)

for thresh in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
    w = l = 0
    for r in rows:
        h1 = f(r.get('home_first_inning_era'))
        a1 = f(r.get('away_first_inning_era'))
        if h1 is None or a1 is None: continue
        if max(h1, a1) > thresh: continue
        result = r.get('nrfi_result')
        if result == 'NRFI': w += 1
        elif result == 'YRFI': l += 1
    report(f'BOTH 1st-inn ERA ≤ {thresh}', w, l)


# =====================================================================
# C. Combined: high NRFI + low 1st-inn ERA on both sides
# =====================================================================
print()
print('=' * 100)
print('C. STACKED: NRFI ≥ X + both 1st-inn ERA ≤ Y')
print('=' * 100)

for nscore, era_thresh in [(85,3.0),(85,2.5),(80,3.0),(80,2.5),(75,3.0),(75,2.5),(70,3.0),(70,2.5)]:
    w = l = 0
    for r in rows:
        s = r.get('nrfi_score') or 0
        if s < nscore: continue
        h1 = f(r.get('home_first_inning_era')); a1 = f(r.get('away_first_inning_era'))
        if h1 is None or a1 is None: continue
        if max(h1, a1) > era_thresh: continue
        result = r.get('nrfi_result')
        if result == 'NRFI': w += 1
        elif result == 'YRFI': l += 1
    report(f'NRFI ≥{nscore} + both 1st-inn ≤{era_thresh}', w, l)


# =====================================================================
# D. xERA stack (season form vs 1st-inning splits)
# =====================================================================
print()
print('=' * 100)
print('D. BOTH STARTERS ELITE xERA + low first-inn ERA')
print('=' * 100)

for xera_thresh, fi_thresh in [(3.5,3.0),(3.0,3.0),(3.5,2.5),(3.0,2.5),(4.0,3.0)]:
    w = l = 0
    for r in rows:
        hx = f(r.get('home_sp_xera')); ax = f(r.get('away_sp_xera'))
        h1 = f(r.get('home_first_inning_era')); a1 = f(r.get('away_first_inning_era'))
        if any(v is None for v in (hx, ax, h1, a1)): continue
        if max(hx, ax) > xera_thresh: continue
        if max(h1, a1) > fi_thresh: continue
        result = r.get('nrfi_result')
        if result == 'NRFI': w += 1
        elif result == 'YRFI': l += 1
    report(f'BOTH xERA ≤{xera_thresh} + 1st-inn ≤{fi_thresh}', w, l)


# =====================================================================
# E. Cold park / cold temp NRFI angle
# =====================================================================
print()
print('=' * 100)
print('E. ENVIRONMENTAL: cold park / cold temp')
print('=' * 100)

# Cold park ≤92, plus NRFI ≥70
for pmax, nmin in [(92,80),(95,80),(92,70),(95,70),(98,70)]:
    w = l = 0
    for r in rows:
        prf = f(r.get('park_run_factor'))
        s = r.get('nrfi_score') or 0
        if prf is None: continue
        if prf > pmax: continue
        if s < nmin: continue
        result = r.get('nrfi_result')
        if result == 'NRFI': w += 1
        elif result == 'YRFI': l += 1
    report(f'park ≤{pmax} + NRFI ≥{nmin}', w, l)

# Cold temp ≤55F
for tmax, nmin in [(45,70),(50,70),(55,70),(50,80)]:
    w = l = 0
    for r in rows:
        t = f(r.get('temperature'))
        s = r.get('nrfi_score') or 0
        if t is None: continue
        if t > tmax: continue
        if s < nmin: continue
        result = r.get('nrfi_result')
        if result == 'NRFI': w += 1
        elif result == 'YRFI': l += 1
    report(f'temp ≤{tmax}F + NRFI ≥{nmin}', w, l)


# =====================================================================
# F. Opponent quality (offense suppression)
# =====================================================================
print()
print('=' * 100)
print('F. OFFENSE SUPPRESSION: both lineups wRC+ ≤ X + NRFI ≥ Y')
print('=' * 100)

for wrc_max, nmin in [(95,70),(95,80),(100,80),(105,70),(100,70)]:
    w = l = 0
    for r in rows:
        hw = f(r.get('home_wrc_plus')); aw = f(r.get('away_wrc_plus'))
        s = r.get('nrfi_score') or 0
        if hw is None or aw is None: continue
        if max(hw, aw) > wrc_max: continue
        if s < nmin: continue
        result = r.get('nrfi_result')
        if result == 'NRFI': w += 1
        elif result == 'YRFI': l += 1
    report(f'BOTH wRC+ ≤{wrc_max} + NRFI ≥{nmin}', w, l)


# =====================================================================
# G. High K rate pitchers (K's kill rallies)
# =====================================================================
print()
print('=' * 100)
print('G. K-RATE STACK: both starters K% ≥ X')
print('=' * 100)

for k_min in [22, 24, 26, 28]:
    w = l = 0
    for r in rows:
        hk = f(r.get('home_sp_k_pct')); ak = f(r.get('away_sp_k_pct'))
        if hk is None or ak is None: continue
        if min(hk, ak) < k_min: continue
        result = r.get('nrfi_result')
        if result == 'NRFI': w += 1
        elif result == 'YRFI': l += 1
    report(f'BOTH starters K% ≥{k_min}%', w, l)


# =====================================================================
# H. L3 ERA recency stack
# =====================================================================
print()
print('=' * 100)
print('H. L3 RECENCY: both starters L3 ERA ≤ X (in form)')
print('=' * 100)

for l3_max in [2.0, 2.5, 3.0, 3.5]:
    w = l = 0
    for r in rows:
        h3 = f(r.get('home_pitcher_last_3_era')); a3 = f(r.get('away_pitcher_last_3_era'))
        if h3 is None or a3 is None: continue
        if max(h3, a3) > l3_max: continue
        result = r.get('nrfi_result')
        if result == 'NRFI': w += 1
        elif result == 'YRFI': l += 1
    report(f'BOTH L3 ERA ≤{l3_max}', w, l)


# =====================================================================
# I. WINNING COHORTS — multi-feature stack discovery
# =====================================================================
print()
print('=' * 100)
print('I. STACKED ANGLES (NRFI + L3 + 1st-inn + xERA combos)')
print('=' * 100)

for nrfi_lo, l3_max, fi_max, xera_max in [
    (80, 3.0, 3.0, 3.5),
    (80, 2.5, 2.5, 3.0),
    (75, 3.0, 3.0, 3.5),
    (75, 2.5, 3.0, 3.5),
    (70, 3.0, 2.5, 3.5),
    (85, 3.5, 3.5, 4.0),
    (85, 3.0, 3.0, 4.0),
]:
    w = l = 0
    for r in rows:
        s = r.get('nrfi_score') or 0
        h3 = f(r.get('home_pitcher_last_3_era')); a3 = f(r.get('away_pitcher_last_3_era'))
        h1 = f(r.get('home_first_inning_era')); a1 = f(r.get('away_first_inning_era'))
        hx = f(r.get('home_sp_xera')); ax = f(r.get('away_sp_xera'))
        if any(v is None for v in (h3, a3, h1, a1, hx, ax)): continue
        if s < nrfi_lo: continue
        if max(h3, a3) > l3_max: continue
        if max(h1, a1) > fi_max: continue
        if max(hx, ax) > xera_max: continue
        result = r.get('nrfi_result')
        if result == 'NRFI': w += 1
        elif result == 'YRFI': l += 1
    report(f'NRFI≥{nrfi_lo}+L3≤{l3_max}+1stinn≤{fi_max}+xERA≤{xera_max}', w, l)


# =====================================================================
# J. NEW NRFI SCORE — synthetic recomputation
# =====================================================================
print()
print('=' * 100)
print('J. SYNTHETIC NRFI SCORE — see if a feature-weighted recompute beats raw nrfi_score')
print('=' * 100)
# Build a score = sum of contributions:
#   +10 if max 1st-inn ERA ≤ 2.5
#   +5  if max 1st-inn ERA ≤ 3.5
#   +8  if max xERA ≤ 3.0
#   +5  if max xERA ≤ 3.5
#   +6  if max L3 ERA ≤ 2.5
#   +4  if max L3 ERA ≤ 3.5
#   +5  if both lineups wRC+ ≤ 95
#   +3  if both lineups wRC+ ≤ 100
#   +5  if both K% ≥ 24
#   +3  if both K% ≥ 22
#   +4  if park ≤ 95
#   +4  if temp ≤ 50
def synth_score(r):
    s = 0
    h1 = f(r.get('home_first_inning_era')); a1 = f(r.get('away_first_inning_era'))
    if h1 is not None and a1 is not None:
        m = max(h1, a1)
        if m <= 2.5: s += 10
        elif m <= 3.5: s += 5
    hx = f(r.get('home_sp_xera')); ax = f(r.get('away_sp_xera'))
    if hx is not None and ax is not None:
        m = max(hx, ax)
        if m <= 3.0: s += 8
        elif m <= 3.5: s += 5
    h3 = f(r.get('home_pitcher_last_3_era')); a3 = f(r.get('away_pitcher_last_3_era'))
    if h3 is not None and a3 is not None:
        m = max(h3, a3)
        if m <= 2.5: s += 6
        elif m <= 3.5: s += 4
    hw = f(r.get('home_wrc_plus')); aw = f(r.get('away_wrc_plus'))
    if hw is not None and aw is not None:
        m = max(hw, aw)
        if m <= 95: s += 5
        elif m <= 100: s += 3
    hk = f(r.get('home_sp_k_pct')); ak = f(r.get('away_sp_k_pct'))
    if hk is not None and ak is not None:
        m = min(hk, ak)
        if m >= 24: s += 5
        elif m >= 22: s += 3
    prf = f(r.get('park_run_factor'))
    if prf is not None and prf <= 95: s += 4
    t = f(r.get('temperature'))
    if t is not None and t <= 50: s += 4
    return s

# Distribute games into synth-score bands and report NRFI hit rate per band
buckets = defaultdict(lambda: {'w':0, 'l':0})
for r in rows:
    ss = synth_score(r)
    if   ss >= 30: b = '30+ (PRIME)'
    elif ss >= 25: b = '25-29 (STRONG)'
    elif ss >= 20: b = '20-24 (LIGHT)'
    elif ss >= 15: b = '15-19'
    elif ss >= 10: b = '10-14'
    else: b = '<10'
    result = r.get('nrfi_result')
    if result == 'NRFI': buckets[b]['w'] += 1
    elif result == 'YRFI': buckets[b]['l'] += 1

for k in ['30+ (PRIME)', '25-29 (STRONG)', '20-24 (LIGHT)', '15-19', '10-14', '<10']:
    report(f'synth {k}', buckets[k]['w'], buckets[k]['l'])

print()
print('=' * 100)
print('DONE — look for ⭐ rows. Anything ≥60% hit AND n ≥ 30 is a candidate to resurface.')
print('=' * 100)
