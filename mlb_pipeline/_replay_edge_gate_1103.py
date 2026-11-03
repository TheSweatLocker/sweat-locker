"""Replay apply_stored_edge_cap over every live primary_play, before vs after.

Column checks pass everything; only a replay against real rows shows what a
gate change actually does (feedback_swallowed_nameerror_is_a_false_condition).
"""
import os, sys, json, requests
from pathlib import Path
from collections import Counter

for ln in (Path(__file__).parent / '.env').read_text(encoding='utf-8', errors='replace').split('\n'):
    if '=' in ln and not ln.lstrip().startswith('#'):
        k, v = ln.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}

from defensive_gates import apply_stored_edge_cap

def old_gate(pp):
    """The pre-fix body, for an exact before/after."""
    if not isinstance(pp, dict): return pp
    from defensive_gates import _MANUAL_MARKERS
    if any(pp.get(k) for k in _MANUAL_MARKERS): return pp
    e = pp.get('_model_edge_pp')
    if not isinstance(e, (int, float)): return pp
    t = str(pp.get('tier') or '').upper()
    if t not in ('PRIME', 'STRONG', 'LEAN'): return pp
    from model_edge import NO_EDGE_PP, NO_EDGE_TIER_CAP
    if float(e) > NO_EDGE_PP: return pp
    out = dict(pp); out['tier'] = NO_EDGE_TIER_CAP
    return out

CTX = ['nhl_game_context','nba_game_context','ncaaf_game_context',
       'ncaab_game_context','mlb_game_context','nfl_game_context']
changed = []
seen = 0
for tbl in CTX:
    off = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=90, params={
            'select': 'game_date,home_team,away_team,primary_play',
            'game_date': 'gte.2026-09-01', 'limit': 1000, 'offset': off})
        if r.status_code != 200: break
        rows = r.json()
        if not rows: break
        for row in rows:
            pp = row.get('primary_play')
            if not isinstance(pp, dict): continue
            seen += 1
            a = str((old_gate(pp) or {}).get('tier') or '')
            b = str((apply_stored_edge_cap(pp) or {}).get('tier') or '')
            if a != b:
                changed.append((tbl, row['game_date'], pp.get('label'),
                                str(pp.get('tier')), a, b, pp.get('_model_edge_pp')))
        off += len(rows)
        if len(rows) < 1000: break

print(f'primary_play rows replayed: {seen}')
print(f'rows where the fix CHANGES the outcome: {len(changed)}')
print()
for tbl, d, lbl, t0, a, b, e in sorted(changed, key=lambda x: x[1], reverse=True):
    print(f'  {d}  {tbl.split("_")[0].upper():<6} {str(lbl)[:30]:<30} '
          f'stored={t0:<8} old_gate->{a:<9} new_gate->{b:<9} edge={float(e):+.2f}pp')
print()
print('by sport:', dict(Counter(c[0].split("_")[0].upper() for c in changed)))
