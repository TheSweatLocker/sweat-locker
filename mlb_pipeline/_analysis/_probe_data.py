"""Quick probe to check graded data availability + column shapes."""
import os, sys, json
from pathlib import Path
from datetime import datetime, timedelta, timezone
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

# Count graded playbook rows
r = requests.get(
    f'{SB}/rest/v1/prop_playbook_decisions',
    headers={**H, 'Prefer': 'count=exact', 'Range-Unit': 'items', 'Range': '0-0'},
    params={
        'select': 'id',
        'game_date': f'gte.{start}',
        'result': 'not.is.null',
    },
    timeout=30,
)
graded = r.headers.get('Content-Range', '').split('/')[-1]
print(f'Graded playbook rows in {start}..today: {graded}')

# Total playbook rows
r2 = requests.get(
    f'{SB}/rest/v1/prop_playbook_decisions',
    headers={**H, 'Prefer': 'count=exact', 'Range-Unit': 'items', 'Range': '0-0'},
    params={'select': 'id', 'game_date': f'gte.{start}'},
    timeout=30,
)
print(f'Total playbook rows in {start}..today: {r2.headers.get("Content-Range", "").split("/")[-1]}')

# Sample columns
r3 = requests.get(
    f'{SB}/rest/v1/prop_playbook_decisions',
    headers=H,
    params={'select': '*', 'limit': '1', 'result': 'not.is.null'},
    timeout=30,
)
sample = r3.json()
if sample:
    print('\nplaybook columns:')
    for k in sorted(sample[0].keys()):
        val = sample[0][k]
        v = str(val)[:80] if val is not None else 'null'
        print(f'  {k}: {v}')

# Count legacy graded rows
r4 = requests.get(
    f'{SB}/rest/v1/mlb_pipeline_props',
    headers={**H, 'Prefer': 'count=exact', 'Range-Unit': 'items', 'Range': '0-0'},
    params={
        'select': 'id',
        'game_date': f'gte.{start}',
        'result': 'not.is.null',
    },
    timeout=30,
)
print(f'\nGraded legacy props {start}..today: {r4.headers.get("Content-Range", "").split("/")[-1]}')

# Sample legacy columns
r5 = requests.get(
    f'{SB}/rest/v1/mlb_pipeline_props',
    headers=H,
    params={'select': '*', 'limit': '1', 'result': 'not.is.null'},
    timeout=30,
)
sample2 = r5.json()
if sample2:
    print('\nlegacy props columns (sample):')
    for k in sorted(sample2[0].keys()):
        val = sample2[0][k]
        v = str(val)[:80] if val is not None else 'null'
        print(f'  {k}: {v}')
