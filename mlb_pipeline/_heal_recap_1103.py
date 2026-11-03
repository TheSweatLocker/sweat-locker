"""One-off: heal today's sweat_card yesterday_recap.potd from the graded surface.

The code fix lives in generate_sweat_card.fetch_yesterday_recap, but today's
card blob was already written at 18:45 with pick=None / result='Pending'.
This patches the stored blob so the home screen is right before the next build.
Readback-verified (feedback_204_is_not_a_write).
"""
import os, sys, json, requests
from pathlib import Path

for ln in (Path(__file__).parent / '.env').read_text(encoding='utf-8', errors='replace').split('\n'):
    if '=' in ln and not ln.lstrip().startswith('#'):
        k, v = ln.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}
APPLY = '--apply' in sys.argv

def get(t, p):
    r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=p, timeout=60)
    r.raise_for_status()
    return r.json()

for day_off in range(0, 4):
    import datetime as dt
    day = (dt.date(2026, 11, 3) - dt.timedelta(days=day_off)).isoformat()
    key = f'sweat_card_{day}'
    rows = get('jerry_cache', {'select': 'data', 'cache_key': f'eq.{key}'})
    if not rows:
        continue
    blob = rows[0]['data']
    if isinstance(blob, str):
        blob = json.loads(blob)
    y = blob.get('yesterday_recap') or {}
    ydate = y.get('date')
    if not ydate:
        print(f'{key}: no yesterday_recap'); continue
    hist = get('daily_best_bet_history',
               {'select': 'sport,game,lean,result,odds_american', 'bet_date': f'eq.{ydate}'})
    if not hist:
        print(f'{key}: recap points at {ydate}, no POTD row there'); continue
    h = hist[0]
    potd = dict(y.get('potd') or {})
    before = (potd.get('pick'), potd.get('result'))
    if h.get('result'):
        potd['result'] = h['result']
    potd['pick'] = potd.get('pick') or h.get('lean')
    potd['matchup'] = potd.get('matchup') or h.get('game')
    potd['sport'] = potd.get('sport') or h.get('sport')
    if h.get('odds_american') is not None:
        potd.setdefault('odds', h['odds_american'])
    after = (potd.get('pick'), potd.get('result'))
    if before == after:
        print(f'{key}: recap({ydate}) already correct {after}'); continue
    print(f'{key}: recap({ydate}) {before} -> {after}')
    if not APPLY:
        continue
    y['potd'] = potd
    blob['yesterday_recap'] = y
    requests.patch(f'{SB}/rest/v1/jerry_cache', headers=H_W,
                   params={'cache_key': f'eq.{key}'},
                   data=json.dumps({'data': blob}), timeout=60)
    # independent readback
    rb = get('jerry_cache', {'select': 'data', 'cache_key': f'eq.{key}'})
    b2 = rb[0]['data']
    if isinstance(b2, str):
        b2 = json.loads(b2)
    got = (b2.get('yesterday_recap') or {}).get('potd') or {}
    ok = (got.get('pick') == potd['pick'] and got.get('result') == potd['result'])
    print(f'    {"CONFIRMED" if ok else "NOT CONFIRMED -- reads " + json.dumps(got)}')
print('\n(dry run — pass --apply to write)' if not APPLY else '')
