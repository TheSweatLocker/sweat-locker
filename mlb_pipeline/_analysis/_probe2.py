"""Probe: result value distributions, odds availability, class counts in signals."""
import os, sys, json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import Counter
import requests

_env = Path(__file__).parent.parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

today = datetime.now(timezone.utc).date()
start = today - timedelta(days=25)

# Playbook result distribution
r = requests.get(
    f'{SB}/rest/v1/prop_playbook_decisions',
    headers=H,
    params={
        'select': 'result,playbook_tier,playbook_side',
        'game_date': f'gte.{start}',
        'result': 'not.is.null',
        'limit': '10000',
    },
    timeout=60,
)
rows = r.json()
print(f'\n== playbook result taxonomy (n={len(rows)}) ==')
print('  result values:', Counter(r['result'] for r in rows))
print('  playbook_tier:', Counter(r['playbook_tier'] for r in rows))
print('  playbook_side:', Counter(r['playbook_side'] for r in rows))

# Cross by tier * result
print('\n  tier x result:')
by_tier = {}
for r in rows:
    t = r['playbook_tier']
    res = r['result']
    by_tier.setdefault(t, Counter())[res] += 1
for t, c in by_tier.items():
    print(f'    {t}: {dict(c)}')

# Legacy props last 25d — check odds & tiers
r2 = requests.get(
    f'{SB}/rest/v1/mlb_pipeline_props',
    headers=H,
    params={
        'select': 'result,tier,conviction,refit_conviction,prop_type,book_over_odds,book_under_odds,direction',
        'game_date': f'gte.{start}',
        'result': 'not.is.null',
        'limit': '10000',
    },
    timeout=60,
)
rows2 = r2.json()
print(f'\n== legacy result taxonomy (n={len(rows2)}) ==')
print('  result values:', Counter(r['result'] for r in rows2))
print('  tier:', Counter(r['tier'] for r in rows2))

# Odds coverage
has_over = sum(1 for r in rows2 if r.get('book_over_odds'))
has_under = sum(1 for r in rows2 if r.get('book_under_odds'))
print(f'  book_over_odds populated: {has_over}/{len(rows2)}')
print(f'  book_under_odds populated: {has_under}/{len(rows2)}')

# tier * result for legacy (matched to playbook window ~8/17+)
r3 = requests.get(
    f'{SB}/rest/v1/mlb_pipeline_props',
    headers=H,
    params={
        'select': 'result,tier,prop_type,book_over_odds,book_under_odds,direction,conviction,refit_conviction',
        'game_date': f'gte.2026-08-01',
        'result': 'not.is.null',
        'limit': '10000',
    },
    timeout=60,
)
rows3 = r3.json()
print(f'\n== legacy last 20d (n={len(rows3)}) ==')
by_tier2 = {}
for r in rows3:
    t = r['tier']
    res = r['result']
    by_tier2.setdefault(t, Counter())[res] += 1
for t in ('PRIME','STRONG','LEAN','SKIP'):
    if t in by_tier2:
        c = by_tier2[t]
        w = c.get('Win', 0) + c.get('W', 0)
        l = c.get('Loss', 0) + c.get('L', 0)
        p = c.get('Push', 0) + c.get('P', 0)
        tot = w + l
        hr = (w/tot*100) if tot else 0
        print(f'    {t}: W={w} L={l} P={p} — hit {hr:.1f}% n={tot}')

# odds distribution on graded picks
odds_vals = []
for r in rows3:
    over_o = r.get('book_over_odds')
    under_o = r.get('book_under_odds')
    if r['direction'] == 'over' and over_o is not None:
        odds_vals.append(int(over_o))
    elif r['direction'] == 'under' and under_o is not None:
        odds_vals.append(int(under_o))
if odds_vals:
    odds_vals.sort()
    n = len(odds_vals)
    print(f'\n  odds coverage: {n} of {len(rows3)} legacy picks have direction-side odds')
    print(f'  odds range: min={odds_vals[0]} p10={odds_vals[n//10]} p50={odds_vals[n//2]} p90={odds_vals[9*n//10]} max={odds_vals[-1]}')

# Prop type coverage
print('\n  prop_type distribution:')
print('   ', Counter(r['prop_type'] for r in rows3).most_common(15))
