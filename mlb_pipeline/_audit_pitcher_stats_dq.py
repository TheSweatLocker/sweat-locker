"""Data-quality audit of mlb_pitcher_stats — how many rows have bogus
default values on key columns?

Key finding from Miller post-mortem (7/25): whiff_rate=10.0 was the
default marker on many pitchers (Scherzer, Kershaw etc. — impossible
for elite arms). Extend the audit to other columns.
"""
import os, requests, sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from collections import Counter
env = Path('.env').read_text()
for line in env.split('\n'):
    if '=' in line and not line.startswith('#'):
        k,v = line.split('=',1); os.environ[k.strip()] = v.strip()
url = os.environ['SUPABASE_URL']; key = os.environ['SUPABASE_KEY']
h = {'apikey': key, 'Authorization': f'Bearer {key}'}

# Pull full pitcher_stats snapshot
rows = []; off = 0
while True:
    r = requests.get(
        f'{url}/rest/v1/mlb_pitcher_stats?select=*&limit=1000&offset={off}',
        headers=h, timeout=30,
    )
    data = r.json()
    if not isinstance(data, list) or not data: break
    rows.extend(data)
    if len(data) < 1000: break
    off += 1000
print(f'Total pitcher_stats rows: {len(rows)}\n')

# For each column, count how many rows have the value that looks like a default
# Suspects: 10.0, 0.10, 0.5, 100, etc.
def count_null(field):
    return sum(1 for r in rows if r.get(field) is None)

def value_distribution(field, top=8):
    vals = [r.get(field) for r in rows if r.get(field) is not None]
    ctr = Counter([round(float(v), 3) if isinstance(v, (int, float)) else str(v) for v in vals])
    return ctr.most_common(top)

print('=== Key column distributions (top values) ===\n')
for col in ('whiff_rate', 'hard_hit_pct', 'barrel_pct', 'avg_fastball_velo',
            'lob_pct', 'gb_pct', 'fb_pct', 'k_pct', 'bb_pct',
            'baa_allowed', 'xba_allowed', 'first_inning_era',
            'last_5_era', 'last_3_era', 'last_3_k_pct',
            'first_inning_ip', 'first_inning_whip'):
    if col not in rows[0]:
        continue
    n_null = count_null(col)
    dist = value_distribution(col, top=5)
    print(f'{col:<24} null={n_null:<4}  top-5: {dist}')

# ─── whiff_rate deep dive ───
print('\n=== whiff_rate deep dive ===')
whiff_vals = [(r['player_name'], r.get('whiff_rate')) for r in rows if r.get('whiff_rate') is not None]
# Buckets
default_10 = [n for n,v in whiff_vals if abs(float(v) - 10.0) < 0.01]
decimals   = [n for n,v in whiff_vals if float(v) < 1.0]
percent    = [n for n,v in whiff_vals if 1.0 <= float(v) < 100.0 and abs(float(v) - 10.0) >= 0.01]
huge       = [n for n,v in whiff_vals if float(v) >= 100.0]

print(f'  total with value: {len(whiff_vals)}')
print(f'  exactly 10.0 (default marker): {len(default_10)}')
print(f'  decimal (<1.0, needs ×100):    {len(decimals)}')
print(f'  percent (1-100 excluding 10):  {len(percent)}')
print(f'  >=100 (garbage):               {len(huge)}')

# Elite pitchers who show 10.0 (impossible)
elite_names = ['Scherzer','Kershaw','Cole','deGrom','Wheeler','Skenes','Sale','Fried',
               'Yamamoto','Snell','Nola','Corbin Burnes','Sonny Gray','Cease','Miller']
print('\n  Elite pitchers stuck at 10.0 default (should have real whiff):')
for n, v in whiff_vals:
    if abs(float(v) - 10.0) < 0.01 and any(e in n for e in elite_names):
        print(f'    {n}: {v}')

# What about updated_at? Are stale rows the ones with defaults?
print('\n=== updated_at distribution for whiff=10.0 rows ===')
from datetime import datetime, timezone
def_rows = [r for r in rows if r.get('whiff_rate') is not None and abs(float(r['whiff_rate']) - 10.0) < 0.01]
month_counter = Counter()
for r in def_rows:
    ts = r.get('updated_at') or ''
    if ts:
        month_counter[ts[:7]] += 1
print('  Default (10.0) rows by update month:')
for m, c in sorted(month_counter.items()):
    print(f'    {m}: {c}')
