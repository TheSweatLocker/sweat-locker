"""ML/side resolver reads for 6/18 slate + grade where games are final."""
import os, requests, json
from dotenv import load_dotenv
load_dotenv()
SU = os.environ.get('SUPABASE_URL'); SK = os.environ.get('SUPABASE_KEY')
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}
DATE = '2026-06-18'

# Pull game results so far
r = requests.get(f'{SU}/rest/v1/mlb_game_results',
    params={'game_date':f'eq.{DATE}','select':'away_team,home_team,away_score,home_score','order':'home_team'},
    headers=H, timeout=10)
finals = {}
for g in r.json():
    hs, as_ = g.get('home_score'), g.get('away_score')
    if hs is not None and as_ is not None:
        mu = f"{g['away_team']} @ {g['home_team']}"
        finals[mu] = {'home_win': hs > as_, 'score': f"{as_}-{hs}", 'margin': hs - as_}

# Pull side resolver reads
r2 = requests.get(f'{SU}/rest/v1/jerry_cache',
    params={'cache_key':f'like.game_read_*_{DATE}','select':'cache_key,data','limit':50},
    headers=H, timeout=15)

print('='*100)
print('  ML / SIDE RESOLVER READS - 6/18 slate')
print('='*100)
print(f'  {"matchup":35s} | {"tier":7s} | {"dir":5s} | {"team":18s} | result')
print('  ' + '-'*100)

tier_grade = {}
for row in r2.json():
    d = row.get('data')
    if isinstance(d, str): d = json.loads(d)
    if not isinstance(d, dict): continue
    mu = d.get('matchup', '?')
    side = d.get('resolver_side') or d.get('side_resolver') or {}
    if not isinstance(side, dict): continue
    tier = (side.get('tier') or '?')
    direction = (side.get('direction') or '?')
    team = (side.get('team') or '')[:18]

    # Grade
    final = finals.get(mu)
    grade = 'pending'
    if final and direction in ('HOME', 'AWAY'):
        if (direction == 'HOME') == final['home_win']:
            grade = f'WIN {final["score"]}'
        else:
            grade = f'LOSS {final["score"]}'
    elif direction not in ('HOME', 'AWAY'):
        grade = 'SKIP'

    mark = '+' if grade.startswith('WIN') else ('-' if grade.startswith('LOSS') else '.')
    print(f'  {mark} {mu[:33]:33s} | {tier:7s} | {direction:5s} | {team:18s} | {grade}')

    bucket = tier_grade.setdefault(tier, {'W': 0, 'L': 0, 'SKIP': 0, 'pending': 0})
    if grade.startswith('WIN'): bucket['W'] += 1
    elif grade.startswith('LOSS'): bucket['L'] += 1
    elif grade == 'SKIP': bucket['SKIP'] += 1
    else: bucket['pending'] += 1

print()
print('  Tier roll-up:')
for tier in sorted(tier_grade.keys()):
    g = tier_grade[tier]
    res = g['W'] + g['L']
    pct = f"{100*g['W']/res:.0f}%" if res else '-'
    print(f'    {tier:8s}: {g["W"]}-{g["L"]} ({pct}) + {g["SKIP"]} skip + {g["pending"]} pending')
