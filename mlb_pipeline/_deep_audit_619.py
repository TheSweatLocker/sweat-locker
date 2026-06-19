"""Deep audit 6/19 morning — ML, Total, RL, Props, models vs actuals."""
import os, requests, json
from collections import defaultdict
import statistics as st
from dotenv import load_dotenv
load_dotenv()
SU = os.environ.get('SUPABASE_URL'); SK = os.environ.get('SUPABASE_KEY')
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

# ──────────────── 1. 6/18 FINALS + MODEL ACCURACY ────────────────
print('='*100)
print('1. 6/18 FINALS + MODEL ACCURACY (totals / spread direction)')
print('='*100)
r = requests.get(f'{SU}/rest/v1/mlb_game_results',
    params={'game_date':'eq.2026-06-18',
            'select':'away_team,home_team,away_score,home_score,close_total,projected_total,model_pred_total,jerry_pred_total,close_spread,projected_spread,model_pred_spread,jerry_pred_spread,signal_confluence_net,total_result,spread_result,home_win,run_line_result,nrfi_result',
            'order':'home_team'},
    headers=H, timeout=10)
games = [g for g in r.json() if g.get('home_score') is not None and g.get('away_score') is not None]
print(f'  resolved: {len(games)}')

# Hand-grade totals (recompute since DB total_runs is sometimes null)
proj_errs, jerry_errs, v4_errs = [], [], []
proj_h=proj_m=jerry_h=jerry_m=v4_h=v4_m=0
sp_h=sp_m=jsp_h=jsp_m=vsp_h=vsp_m=0
ml_pred = {'projected':0, 'jerry':0, 'v4':0}
ml_correct = {'projected':0, 'jerry':0, 'v4':0}

for g in games:
    actual = g['away_score'] + g['home_score']
    line = g.get('close_total')
    proj = g.get('projected_total'); jerry = g.get('jerry_pred_total'); v4 = g.get('model_pred_total')
    if proj is not None and actual is not None:
        proj_errs.append(proj - actual)
    if jerry is not None and actual is not None:
        jerry_errs.append(jerry - actual)
    if v4 is not None and actual is not None:
        v4_errs.append(v4 - actual)
    if line is not None and actual != line:
        for v, label in [(proj,'p'), (jerry,'j'), (v4,'v')]:
            if v is None: continue
            pd = 'O' if v > line else 'U'
            ad = 'O' if actual > line else 'U'
            if pd == ad:
                if label=='p': proj_h+=1
                elif label=='j': jerry_h+=1
                else: v4_h+=1
            else:
                if label=='p': proj_m+=1
                elif label=='j': jerry_m+=1
                else: v4_m+=1

    actual_home = g['home_score'] > g['away_score']
    for v, label in [(g.get('projected_spread'),'p'),
                     (g.get('jerry_pred_spread'),'j'),
                     (g.get('model_pred_spread'),'v')]:
        if v is None: continue
        ml_pred[{'p':'projected','j':'jerry','v':'v4'}[label]] += 1
        pred_home = float(v) > 0
        if pred_home == actual_home:
            ml_correct[{'p':'projected','j':'jerry','v':'v4'}[label]] += 1
            if label=='p': sp_h+=1
            elif label=='j': jsp_h+=1
            else: vsp_h+=1
        else:
            if label=='p': sp_m+=1
            elif label=='j': jsp_m+=1
            else: vsp_m+=1

print()
print(f'  TOTALS DIRECTION ACCURACY:')
print(f'    proj:  {proj_h}-{proj_m} ({100*proj_h/max(1,proj_h+proj_m):.0f}%)  mean err {st.mean(proj_errs):+.2f}')
print(f'    jerry: {jerry_h}-{jerry_m} ({100*jerry_h/max(1,jerry_h+jerry_m):.0f}%)  mean err {st.mean(jerry_errs):+.2f}')
print(f'    v4:    {v4_h}-{v4_m} ({100*v4_h/max(1,v4_h+v4_m):.0f}%)  mean err {st.mean(v4_errs):+.2f}')

print()
print(f'  SPREAD DIRECTION (predicts ML winner):')
print(f'    proj:  {sp_h}-{sp_m} ({100*sp_h/max(1,sp_h+sp_m):.0f}%)')
print(f'    jerry: {jsp_h}-{jsp_m} ({100*jsp_h/max(1,jsp_h+jsp_m):.0f}%)')
print(f'    v4:    {vsp_h}-{vsp_m} ({100*vsp_h/max(1,vsp_h+vsp_m):.0f}%)')

# Total / spread / RL roll-up
tot_res = defaultdict(int)
sp_res = defaultdict(int)
rl_res = defaultdict(int)
ml_home_won = 0
ml_away_won = 0
nrfi_yes = 0; nrfi_no = 0
for g in games:
    tot_res[g.get('total_result') or '?'] += 1
    sp_res[g.get('spread_result') or '?'] += 1
    rl_res[g.get('run_line_result') or '?'] += 1
    if g.get('home_win'): ml_home_won += 1
    elif g.get('home_win') is False: ml_away_won += 1
    nrfi_v = g.get('nrfi_result') or ''
    if 'NRFI' in nrfi_v.upper() and 'YRFI' not in nrfi_v.upper(): nrfi_yes += 1
    elif 'YRFI' in nrfi_v.upper(): nrfi_no += 1

print()
print(f'  SLATE RESULTS:')
print(f'    Totals: {dict(tot_res)}')
print(f'    Spread: {dict(sp_res)}')
print(f'    RL:     {dict(rl_res)}')
print(f'    ML:     HOME {ml_home_won} / AWAY {ml_away_won}')
print(f'    NRFI:   {nrfi_yes} held / {nrfi_no} YRFI'.format(nrfi_yes=nrfi_yes, nrfi_no=nrfi_no))

# ──────────────── 2. RESOLVER TIER GRADES ────────────────
print()
print('='*100)
print('2. RESOLVER TIER GRADES (side + total)')
print('='*100)
r2 = requests.get(f'{SU}/rest/v1/jerry_cache',
    params={'cache_key':'like.game_read_*_2026-06-18','select':'data','limit':30},
    headers=H, timeout=15)
reads = r2.json()
finals_by_mu = {}
for g in games:
    mu = f"{g['away_team']} @ {g['home_team']}"
    finals_by_mu[mu] = {'home_win': g['home_score']>g['away_score'],
                       'total': g['home_score']+g['away_score'],
                       'line': g.get('close_total'),
                       'score': f"{g['away_score']}-{g['home_score']}",
                       'margin': g['home_score']-g['away_score']}

side_grade = defaultdict(lambda: {'W':0,'L':0,'SKIP':0,'PEND':0})
tot_grade = defaultdict(lambda: {'W':0,'L':0,'P':0,'SKIP':0,'PEND':0})
rl_grade = defaultdict(lambda: {'W':0,'L':0,'P':0,'SKIP':0,'PEND':0})

for row in reads:
    d = row.get('data') or {}
    if isinstance(d,str): d=json.loads(d)
    mu = d.get('matchup','')
    final = finals_by_mu.get(mu)
    side = d.get('resolver_side') or {}
    side_tier = side.get('tier','?')
    side_dir = side.get('direction','?')
    if not final:
        side_grade[side_tier]['PEND'] += 1
    elif side_dir not in ('HOME','AWAY'):
        side_grade[side_tier]['SKIP'] += 1
    else:
        won = (side_dir == 'HOME') == final['home_win']
        side_grade[side_tier]['W' if won else 'L'] += 1

    tot = d.get('resolver') or {}
    tot_tier = tot.get('tier','?')
    tot_dir = tot.get('direction','?')
    if not final or not final.get('line'):
        tot_grade[tot_tier]['PEND'] += 1
    elif tot_dir not in ('OVER','UNDER'):
        tot_grade[tot_tier]['SKIP'] += 1
    else:
        actual = final['total']; line = float(final['line'])
        if actual == line:
            tot_grade[tot_tier]['P'] += 1
        elif (tot_dir=='OVER' and actual>line) or (tot_dir=='UNDER' and actual<line):
            tot_grade[tot_tier]['W'] += 1
        else:
            tot_grade[tot_tier]['L'] += 1

    # RL grade — approximate using margin vs ±1.5
    if final and side_dir in ('HOME','AWAY'):
        margin = final['margin']
        if side_dir == 'HOME':
            rl_won = margin > 1.5
            rl_lost = margin < -1.5
            rl_push = abs(margin) == 1.5
        else:
            rl_won = margin < 1.5
            rl_lost = margin > 1.5
            rl_push = abs(margin) == 1.5
        if rl_won: rl_grade[side_tier]['W'] += 1
        elif rl_lost: rl_grade[side_tier]['L'] += 1
        elif rl_push: rl_grade[side_tier]['P'] += 1
    elif not final:
        rl_grade[side_tier]['PEND'] += 1
    else:
        rl_grade[side_tier]['SKIP'] += 1

print()
print('  SIDE (ML) resolver tier:')
for t,c in side_grade.items():
    n=c['W']+c['L']
    pct=f"{100*c['W']/n:.0f}%" if n else '-'
    print(f'    {t:8s}: {c["W"]}-{c["L"]} ({pct})  + {c["SKIP"]} skip + {c["PEND"]} pend')

print()
print('  RL (run-line) approx (side direction × ±1.5 margin):')
for t,c in rl_grade.items():
    n=c['W']+c['L']
    pct=f"{100*c['W']/n:.0f}%" if n else '-'
    print(f'    {t:8s}: {c["W"]}-{c["L"]}-{c["P"]} ({pct})  + {c["SKIP"]} skip + {c["PEND"]} pend')

print()
print('  TOTAL resolver tier:')
for t,c in tot_grade.items():
    n=c['W']+c['L']
    pct=f"{100*c['W']/n:.0f}%" if n else '-'
    print(f'    {t:8s}: {c["W"]}-{c["L"]}-{c["P"]} ({pct})  + {c["SKIP"]} skip + {c["PEND"]} pend')

# ──────────────── 3. PROP PIPELINE GRADES ────────────────
print()
print('='*100)
print('3. PROP PIPELINE GRADES (mlb_pipeline_props, 6/18)')
print('='*100)
r3 = requests.get(f'{SU}/rest/v1/mlb_pipeline_props',
    params={'game_date':'eq.2026-06-18','tier':'in.(PRIME,STRONG,LEAN)',
            'select':'player_name,prop_type,tier,conviction,direction,prop_line,final_value,result',
            'order':'tier.asc,conviction.desc.nullslast','limit':200},
    headers=H, timeout=10)
props = r3.json() if isinstance(r3.json(),list) else []
print(f'  PRIME/STRONG/LEAN props: {len(props)}')

# By tier × type
by_tier_type = defaultdict(lambda: defaultdict(lambda: {'W':0,'L':0,'P':0,'pend':0}))
by_tier = defaultdict(lambda: {'W':0,'L':0,'P':0,'pend':0})
for p in props:
    tier = (p.get('tier') or '?').upper()
    ptype = p.get('prop_type') or '?'
    res = p.get('result')
    bucket = by_tier_type[tier][ptype]
    big = by_tier[tier]
    if res == 'Win': bucket['W'] += 1; big['W'] += 1
    elif res == 'Loss': bucket['L'] += 1; big['L'] += 1
    elif res == 'Push': bucket['P'] += 1; big['P'] += 1
    else: bucket['pend'] += 1; big['pend'] += 1

print()
print('  Big tier roll-up:')
for t, c in by_tier.items():
    n = c['W'] + c['L']
    pct = f"{100*c['W']/n:.0f}%" if n else '-'
    print(f'    {t:8s}: {c["W"]}-{c["L"]}-{c["P"]} ({pct})  + {c["pend"]} pending')

print()
print('  PRIME by prop_type:')
for ptype in sorted(by_tier_type.get('PRIME', {}).keys()):
    c = by_tier_type['PRIME'][ptype]
    n = c['W'] + c['L']
    pct = f"{100*c['W']/n:.0f}%" if n else '-'
    print(f'    {ptype:14s}: {c["W"]}-{c["L"]}-{c["P"]} ({pct})  + {c["pend"]} pending')

print()
print('  STRONG by prop_type:')
for ptype in sorted(by_tier_type.get('STRONG', {}).keys()):
    c = by_tier_type['STRONG'][ptype]
    n = c['W'] + c['L']
    pct = f"{100*c['W']/n:.0f}%" if n else '-'
    print(f'    {ptype:14s}: {c["W"]}-{c["L"]}-{c["P"]} ({pct})  + {c["pend"]} pending')
