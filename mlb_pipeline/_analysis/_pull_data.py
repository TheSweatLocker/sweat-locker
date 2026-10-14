"""Pull full playbook + legacy graded rows for 21d window into JSON on disk."""
import os, json, time
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

OUT = Path(__file__).parent
today = datetime.now(timezone.utc).date()
start = today - timedelta(days=25)


def page_all(url, params):
    """Paginate PostgREST until all rows exhausted."""
    out = []
    limit = 1000
    offset = 0
    while True:
        p = {**params, 'limit': str(limit), 'offset': str(offset)}
        r = requests.get(url, headers=H, params=p, timeout=60)
        if r.status_code != 200:
            print(f'  ERROR {r.status_code}: {r.text[:200]}')
            break
        rows = r.json()
        if not rows:
            break
        out.extend(rows)
        if len(rows) < limit:
            break
        offset += limit
    return out


# Playbook — all fields
print('Fetching playbook decisions...')
pb = page_all(
    f'{SB}/rest/v1/prop_playbook_decisions',
    {
        'select': '*',
        'game_date': f'gte.{start}',
    },
)
print(f'  playbook: {len(pb)} rows')
(OUT / 'playbook.json').write_text(json.dumps(pb))

# Legacy — all fields we need
print('Fetching legacy props...')
leg = page_all(
    f'{SB}/rest/v1/mlb_pipeline_props',
    {
        'select': 'id,game_date,game_id,player_name,player_team,prop_type,'
                  'direction,prop_line,tier,conviction,refit_conviction,'
                  'signals,result,final_value,book_over_odds,book_under_odds,'
                  'stack_alert,matchup',
        'game_date': f'gte.{start}',
    },
)
print(f'  legacy: {len(leg)} rows')
(OUT / 'legacy.json').write_text(json.dumps(leg))

# Sample stats
from collections import Counter
graded_pb = [r for r in pb if r.get('result')]
graded_leg = [r for r in leg if r.get('result')]
print(f'\nGraded playbook: {len(graded_pb)}')
print(f'Graded legacy: {len(graded_leg)}')
print(f'  playbook tier dist: {dict(Counter(r["playbook_tier"] for r in graded_pb))}')
print(f'  legacy tier dist: {dict(Counter(r["tier"] for r in graded_leg))}')
