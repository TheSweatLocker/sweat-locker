"""When the SIDE resolver says STRONG and v4 dissents, what actually happens?

Cohorts:
  - 'STRONG-aligned': v3 + jerry + v4 all pick same direction (clean consensus)
  - 'STRONG-v4 dissent': v3 + jerry agree, v4 picks opposite (model majority WITHOUT v4)
  - 'STRONG-v3 dissent': v4 + jerry agree, v3 dissents (model majority WITHOUT v3)
  - 'STRONG-jerry dissent': v3 + v4 agree, jerry dissents

For each, compute ML direction accuracy. If v4 dissent → 50% then v4 dissent is a
real fade signal and STRONG picks should require v4 agreement. If v4 dissent stays
~62%, then ignore v4 dissent and trust resolver.
"""
import os, requests
from collections import Counter
from datetime import date, timedelta
from dotenv import load_dotenv
load_dotenv()
SU = os.environ['SUPABASE_URL']; SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

today = date.today()
since = (today - timedelta(days=90)).isoformat()

r = requests.get(f'{SU}/rest/v1/mlb_game_results',
    params={'game_date': f'gte.{since}',
            'select': ('game_date,home_team,away_team,home_score,away_score,'
                       'close_spread,projected_spread,model_pred_spread,jerry_pred_spread,'
                       'signal_confluence_net'),
            'order': 'game_date.asc'},
    headers=H, timeout=30)
games = [g for g in r.json() if g.get('home_score') is not None]
print(f'pulled {len(games)} graded games last 90d')

def direction(pred):
    """Returns 'HOME' if pred predicts home wins by margin, 'AWAY' if away wins, None otherwise.
    Spread predictions are home-side: negative = home favored, positive = home dog.
    We use 0 as the cutoff for direction (favors home > 0 line = home wins outright)."""
    if pred is None: return None
    try: p = float(pred)
    except: return None
    # If predicted_spread is < 0 (home favored by model), model picks home
    # If > 0 (home dog per model), model picks away
    if p < 0: return 'HOME'
    if p > 0: return 'AWAY'
    return None

def models_for(g):
    v3 = direction(g.get('projected_spread'))
    v4 = direction(g.get('model_pred_spread'))
    jr = direction(g.get('jerry_pred_spread'))
    return v3, v4, jr

def actual_winner(g):
    if g['home_score'] > g['away_score']: return 'HOME'
    if g['home_score'] < g['away_score']: return 'AWAY'
    return None

cohorts = Counter()
hits = Counter()
for g in games:
    v3, v4, jr = models_for(g)
    actual = actual_winner(g)
    if actual is None: continue
    votes = [d for d in (v3, v4, jr) if d in ('HOME','AWAY')]
    if len(votes) < 2: continue
    home = votes.count('HOME'); away = votes.count('AWAY')
    if home == away: continue  # no majority
    majority = 'HOME' if home > away else 'AWAY'

    # All 3 unanimous?
    if home == 3 or away == 3:
        cohort = 'ALL-3 unanimous'
    else:
        # Who dissented?
        dissenters = []
        if v3 and v3 != majority: dissenters.append('v3')
        if v4 and v4 != majority: dissenters.append('v4')
        if jr and jr != majority: dissenters.append('jerry')
        if not dissenters: cohort = 'ALL-3 unanimous'
        elif 'v4' in dissenters and len(dissenters)==1: cohort = '2/3 majority, v4 dissents'
        elif 'v3' in dissenters and len(dissenters)==1: cohort = '2/3 majority, v3 dissents'
        elif 'jerry' in dissenters and len(dissenters)==1: cohort = '2/3 majority, jerry dissents'
        else: cohort = 'other'
    cohorts[cohort] += 1
    if majority == actual:
        hits[cohort] += 1

print()
print(f'{"cohort":40s} {"hits":>5s} {"n":>5s} {"hit_rate":>9s}')
print('-'*70)
total_h = total_n = 0
for c, n in sorted(cohorts.items(), key=lambda x: -x[1]):
    h = hits.get(c, 0)
    rate = 100*h/n if n else 0
    total_h += h; total_n += n
    print(f'{c:40s} {h:>5d} {n:>5d} {rate:>8.1f}%')
print('-'*70)
overall = 100*total_h/total_n if total_n else 0
print(f'{"OVERALL (any model majority)":40s} {total_h:>5d} {total_n:>5d} {overall:>8.1f}%')

# Now restrict to STRONG-equivalent: cohort confluence loud (proxy for resolver STRONG)
# signal_confluence_net is a directional cohort count — values >= 4 are typically STRONG
print()
print('='*70)
print('NOW filtering to STRONG-equivalent (|signal_confluence_net| >= 4)')
print('='*70)
cohorts = Counter(); hits = Counter()
for g in games:
    snc = g.get('signal_confluence_net')
    if snc is None or abs(snc) < 4: continue
    v3, v4, jr = models_for(g)
    actual = actual_winner(g)
    if actual is None: continue
    votes = [d for d in (v3, v4, jr) if d in ('HOME','AWAY')]
    if len(votes) < 2: continue
    home = votes.count('HOME'); away = votes.count('AWAY')
    if home == away: continue
    majority = 'HOME' if home > away else 'AWAY'
    if home == 3 or away == 3:
        cohort = 'STRONG + all-3 agree'
    else:
        dissenters = []
        if v3 and v3 != majority: dissenters.append('v3')
        if v4 and v4 != majority: dissenters.append('v4')
        if jr and jr != majority: dissenters.append('jerry')
        if 'v4' in dissenters and len(dissenters)==1: cohort = 'STRONG + v4 dissents'
        elif 'v3' in dissenters and len(dissenters)==1: cohort = 'STRONG + v3 dissents'
        elif 'jerry' in dissenters and len(dissenters)==1: cohort = 'STRONG + jerry dissents'
        else: cohort = 'STRONG + other'
    cohorts[cohort] += 1
    if majority == actual: hits[cohort] += 1

print()
print(f'{"cohort":40s} {"hits":>5s} {"n":>5s} {"hit_rate":>9s}')
print('-'*70)
for c, n in sorted(cohorts.items(), key=lambda x: -x[1]):
    h = hits.get(c, 0)
    rate = 100*h/n if n else 0
    print(f'{c:40s} {h:>5d} {n:>5d} {rate:>8.1f}%')
