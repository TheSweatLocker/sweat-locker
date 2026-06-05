"""Four advanced cohort backtests (n=640 graded games):
  A. Park x pitcher type — Coors x flyball vs Petco x flyball, etc.
  B. Offense tilt — L7 wRC+ hot AND L14 cold (rebound) vs reverse
  C. Rest x ace — long-rest ace cohort + dog cross
  D. Bullpen tax x SIDE — bp_relievers_3d high as ML fade
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

print('Loading graded games (full feature set)...')
sel = (
    'game_date,home_team,away_team,home_score,away_score,'
    'close_spread,close_total,open_spread,open_total,'
    'home_ml_close,away_ml_close,'
    'park_run_factor,venue,'
    'home_sp_gb_pct,away_sp_gb_pct,home_sp_whiff_rate,away_sp_whiff_rate,'
    'home_sp_era,away_sp_era,home_sp_xera,away_sp_xera,'
    'home_sp_days_rest,away_sp_days_rest,'
    'home_sp_last5_era,away_sp_last5_era,'
    'home_pitcher_last_3_era,away_pitcher_last_3_era,'
    'home_bp_relievers_3d,away_bp_relievers_3d,home_bullpen_era,away_bullpen_era,'
    'home_wrc_plus,away_wrc_plus,home_wrc_proxy_l14,away_wrc_proxy_l14,'
    'home_ops_last7,away_ops_last7,home_ops_last14,away_ops_last14,'
    'home_last5_runs_per_game,away_last5_runs_per_game,'
    'home_last10_runs_per_game,away_last10_runs_per_game,'
    'home_runs_per_game,away_runs_per_game,'
    'home_offense_drift,away_offense_drift,'
    'signal_confluence_net,xera_gap_runs:home_sp_xera,'
    'home_team_barrel_pct,away_team_barrel_pct,'
    'wind_blowing_in,wind_mph,temperature'
)
rows = get(f'/rest/v1/mlb_game_results?close_spread=not.is.null&select={sel}&order=game_date.asc')
rows = [r for r in rows if r.get('home_score') is not None]
print(f'n={len(rows)} graded games')


def rl_outcome(r, pick_home):
    cs = float(r['close_spread'])
    margin = (r['home_score'] or 0) - (r['away_score'] or 0)
    home_is_fav = cs < 0
    if pick_home == home_is_fav:
        return 'cover' if (margin >= 2 if pick_home else -margin >= 2) else 'fail'
    else:
        return 'cover' if (margin >= -1 if pick_home else -margin >= -1) else 'fail'

def ml_outcome(r, pick_home):
    margin = (r['home_score'] or 0) - (r['away_score'] or 0)
    return 'win' if (margin > 0 if pick_home else margin < 0) else 'loss'

def total_outcome(r, pick_over):
    actual = (r['home_score'] or 0) + (r['away_score'] or 0)
    close = r.get('close_total')
    if close is None: return None
    close = float(close)
    if abs(actual - close) < 0.01: return 'push'
    if pick_over: return 'win' if actual > close else 'loss'
    return 'win' if actual < close else 'loss'

def f(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def report(name, w, l, price=-110):
    tot = w + l
    if tot == 0:
        print(f'  {name:>60s}  n=0'); return
    hit = w/tot*100
    if price > 0: payout = price/100
    else: payout = 100/abs(price)
    ev = (w * payout - l) / tot
    print(f'  {name:>60s}  n={tot:>4d}  {w}-{l}  hit={hit:>5.1f}%  EV@{price}={ev:+.3f}')


# ========================================================================
# A. PARK x PITCHER TYPE (Coors x flyball, Petco x flyball, hitter park x low-velo)
# ========================================================================
print()
print('=' * 110)
print('A. PARK x PITCHER TYPE — does park + pitcher-style stack predict total better?')
print('=' * 110)

# Park bands
def park_band(prf):
    if prf is None: return None
    p = float(prf)
    if p >= 115: return 'hitter_extreme'   # Coors-ish
    if p >= 105: return 'hitter_lean'
    if p <= 90:  return 'pitcher_extreme'  # Petco/Marlins
    if p <= 95:  return 'pitcher_lean'
    return 'neutral'

# Pitcher type by GB% (per game, pick the higher-pressure side)
# We'll evaluate each game by COMBINED starter profile: avg GB% across both
# OVER bias: low GB% (flyball) pitchers — balls leave park
# UNDER bias: high GB% (groundball) pitchers — fewer HR
def gb_combined(r):
    h = f(r.get('home_sp_gb_pct')); a = f(r.get('away_sp_gb_pct'))
    vals = [v for v in [h, a] if v is not None]
    if not vals: return None
    return sum(vals) / len(vals)

park_x_gb = defaultdict(lambda: {'over_w':0,'over_l':0,'under_w':0,'under_l':0})

for r in rows:
    pb = park_band(r.get('park_run_factor'))
    gb = gb_combined(r)
    if pb is None or gb is None: continue
    # GB% bucket: high >= 0.50, low <= 0.40, mid otherwise
    if gb >= 0.50: gbb = 'high_GB'
    elif gb <= 0.40: gbb = 'low_GB(flyball)'
    else: gbb = 'mid_GB'
    key = f'{pb} x {gbb}'
    ou = total_outcome(r, True)
    uo = total_outcome(r, False)
    if ou == 'win': park_x_gb[key]['over_w'] += 1
    elif ou == 'loss': park_x_gb[key]['over_l'] += 1
    if uo == 'win': park_x_gb[key]['under_w'] += 1
    elif uo == 'loss': park_x_gb[key]['under_l'] += 1

for key in sorted(park_x_gb.keys()):
    d = park_x_gb[key]
    if d['over_w'] + d['over_l'] < 10: continue  # skip thin samples
    report(f'{key}: OVER', d['over_w'], d['over_l'])
    report(f'{key}: UNDER', d['under_w'], d['under_l'])


# ========================================================================
# B. OFFENSE TILT — L7/L14 vs season cohorts
# ========================================================================
print()
print('=' * 110)
print('B. OFFENSE TILT — L7 hot + L14 cold (rebound) vs reverse (slumping)')
print('=' * 110)

# Use ops_last7 vs ops_last14 vs season ops as proxies.
# We don't have ops_season per side; use wrc_plus and ops_last14 / ops_last7 deltas.
# Cohort: "rebound" = L7 ops >> L14 ops (>=0.040 delta) — recent surge
# Cohort: "slump" = L7 ops << L14 ops (>=-0.040 delta) — recent collapse

def offense_state(r, side):
    o7 = f(r.get(f'{side}_ops_last7')); o14 = f(r.get(f'{side}_ops_last14'))
    if o7 is None or o14 is None: return None
    delta = o7 - o14
    if delta >= 0.040: return 'rebound'
    if delta <= -0.040: return 'slump'
    return 'steady'

cohorts = defaultdict(lambda: {'ml_w':0,'ml_l':0,'rl_cov':0,'rl_fail':0,'tot_over_w':0,'tot_over_l':0})
for r in rows:
    h_state = offense_state(r, 'home')
    a_state = offense_state(r, 'away')
    if h_state is None or a_state is None: continue
    # Cohort key by side states
    key = f'home={h_state}/away={a_state}'
    # If home is rebound + away is slump → pick home
    if h_state == 'rebound' and a_state == 'slump':
        ml = ml_outcome(r, True); rl = rl_outcome(r, True)
        cohorts[key]['ml_w' if ml=='win' else 'ml_l'] += 1
        cohorts[key]['rl_cov' if rl=='cover' else 'rl_fail'] += 1
    elif h_state == 'slump' and a_state == 'rebound':
        ml = ml_outcome(r, False); rl = rl_outcome(r, False)
        cohorts[key]['ml_w' if ml=='win' else 'ml_l'] += 1
        cohorts[key]['rl_cov' if rl=='cover' else 'rl_fail'] += 1
    # Totals: if both rebound → over bias; both slump → under bias
    if h_state == 'rebound' and a_state == 'rebound':
        ou = total_outcome(r, True)
        cohorts['both_rebound']['tot_over_w' if ou=='win' else 'tot_over_l'] += 1
    elif h_state == 'slump' and a_state == 'slump':
        ou = total_outcome(r, False)
        cohorts['both_slump']['tot_over_w' if ou=='win' else 'tot_over_l'] += 1  # actually UNDER hits

for k, d in sorted(cohorts.items()):
    if k == 'both_rebound':
        report(f'{k}: pick OVER', d['tot_over_w'], d['tot_over_l'])
    elif k == 'both_slump':
        report(f'{k}: pick UNDER', d['tot_over_w'], d['tot_over_l'])
    else:
        report(f'{k}: pick rebound side ML', d['ml_w'], d['ml_l'])
        report(f'{k}: pick rebound side RL', d['rl_cov'], d['rl_fail'])


# ========================================================================
# C. REST x ACE — long-rest + ace SP cover ATS better?
# ========================================================================
print()
print('=' * 110)
print('C. REST x ACE — long rest (5+) AND ace (xERA <= 3.40 OR season ERA <= 3.50)')
print('=' * 110)

# Pick the long-rest ace side ML/RL. Cohort split: ace is fav vs ace is dog.
for ace_xera_thresh in [3.40, 3.70]:
    print(f'  -- Ace xERA threshold <= {ace_xera_thresh} --')
    fav_w = fav_l = dog_w = dog_l = 0
    fav_rl_w = fav_rl_l = dog_rl_w = dog_rl_l = 0
    for r in rows:
        cs = r.get('close_spread')
        if cs is None: continue
        cs_f = float(cs)
        home_is_fav = cs_f < 0
        for side, pick_home in [('home', True), ('away', False)]:
            rest = f(r.get(f'{side}_sp_days_rest'))
            xera = f(r.get(f'{side}_sp_xera'))
            if rest is None or xera is None: continue
            if rest < 5 or xera > ace_xera_thresh: continue
            ml = ml_outcome(r, pick_home)
            rl = rl_outcome(r, pick_home)
            is_fav = (pick_home == home_is_fav)
            if is_fav:
                if ml == 'win': fav_w += 1
                else: fav_l += 1
                if rl == 'cover': fav_rl_w += 1
                else: fav_rl_l += 1
            else:
                if ml == 'win': dog_w += 1
                else: dog_l += 1
                if rl == 'cover': dog_rl_w += 1
                else: dog_rl_l += 1
    report(f'long-rest ace as FAV ML', fav_w, fav_l)
    report(f'long-rest ace as FAV RL', fav_rl_w, fav_rl_l)
    report(f'long-rest ace as DOG ML', dog_w, dog_l)
    report(f'long-rest ace as DOG RL', dog_rl_w, dog_rl_l)


# ========================================================================
# D. BULLPEN TAX x SIDE — bp_relievers_3d >= 4 (gassed BP) fade their team?
# ========================================================================
print()
print('=' * 110)
print('D. BULLPEN TAX x SIDE — fade team with bp_relievers_3d >= 4 (recent BP overuse)')
print('=' * 110)

for tax_thresh in [3, 4, 5]:
    print(f'  -- Tax threshold: bp_relievers_3d >= {tax_thresh} --')
    fade_w = fade_l = 0
    fade_rl_w = fade_rl_l = 0
    fade_over_w = fade_over_l = 0
    for r in rows:
        cs = r.get('close_spread')
        if cs is None: continue
        h_bp = f(r.get('home_bp_relievers_3d'))
        a_bp = f(r.get('away_bp_relievers_3d'))
        if h_bp is None and a_bp is None: continue
        # Determine which side has tax (one-sided fade — both gassed cancels out)
        h_tax = (h_bp is not None and h_bp >= tax_thresh)
        a_tax = (a_bp is not None and a_bp >= tax_thresh)
        if h_tax == a_tax: continue
        fade_home = h_tax  # if home is taxed, fade home → pick away
        pick_home_opposite = not fade_home
        ml = ml_outcome(r, pick_home_opposite)
        rl = rl_outcome(r, pick_home_opposite)
        if ml == 'win': fade_w += 1
        else: fade_l += 1
        if rl == 'cover': fade_rl_w += 1
        else: fade_rl_l += 1
        # Totals: tax side bullpen suggests OVER bias
        ou = total_outcome(r, True)
        if ou == 'win': fade_over_w += 1
        elif ou == 'loss': fade_over_l += 1
    report(f'fade taxed BP team ML', fade_w, fade_l)
    report(f'fade taxed BP team RL', fade_rl_w, fade_rl_l)
    report(f'taxed BP OVER total', fade_over_w, fade_over_l)


# ========================================================================
# E. BONUS — confluence net=4 x SIDE rest cohort intersection
# ========================================================================
print()
print('=' * 110)
print('E. BONUS — confluence net=4 DOG + long-rest dog SP (stacking)')
print('=' * 110)

cov = fail = 0
for r in rows:
    cn = r.get('signal_confluence_net')
    cs = r.get('close_spread')
    if cn is None or cs is None: continue
    try:
        cn_abs = abs(int(cn))
        cn_i = int(cn)
    except (TypeError, ValueError):
        continue
    if cn_abs != 4: continue
    cs_f = float(cs)
    home_is_fav = cs_f < 0
    # confluence dir (positive = home, negative = away)
    conf_picks_home = cn_i > 0
    is_dog = (conf_picks_home != home_is_fav)
    if not is_dog: continue
    # Now check dog side starter rest
    dog_side = 'home' if conf_picks_home else 'away'
    rest = f(r.get(f'{dog_side}_sp_days_rest'))
    if rest is None or rest < 5: continue
    rl = rl_outcome(r, conf_picks_home)
    if rl == 'cover': cov += 1
    else: fail += 1
report('net=4 DOG + dog SP long-rest RL', cov, fail)

print()
print('=' * 110)
print('DONE')
