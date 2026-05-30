"""One-off fix: today's props shipped without book-line recalibration.
attach_book_lines + recalibrate_props_with_book_lines never fired in the
cron run that produced today's props (root cause TBD — Odds API works,
code is in place, but no props have _book_line / _pre_recal_tier traces).

Pulls today's props, runs the missing recalibration step, PATCHes each
affected prop in mlb_pipeline_props with the new book_line, conviction,
tier, and signals. Non-destructive — only updates rows where the recal
made a change.
"""
import os
import sys
import io
import json
import urllib.request
from dotenv import load_dotenv

load_dotenv()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_props import (
    attach_book_lines,
    recalibrate_props_with_book_lines,
    PROP_MARKET_MAP,
)

URL = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def get(p):
    with urllib.request.urlopen(urllib.request.Request(URL + p, headers=H), timeout=20) as r:
        return json.loads(r.read())


def patch_prop(prop_id, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        URL + f'/rest/v1/mlb_pipeline_props?id=eq.{prop_id}',
        data=body, method='PATCH',
        headers={**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


# Pull today's pitcher props (only types in PROP_MARKET_MAP are eligible for recal)
props = get('/rest/v1/mlb_pipeline_props?game_date=eq.2026-05-30&select=*')
pitcher_props = [p for p in props if p.get('prop_type') in PROP_MARKET_MAP]
print(f'Total props today: {len(props)}, pitcher props in scope: {len(pitcher_props)}')

# Snapshot pre-recal state so we can detect changes
pre = {p['id']: {'tier': p.get('tier'), 'conviction': p.get('conviction'),
                 'prop_line': p.get('prop_line'), 'book_line': p.get('book_line')}
       for p in pitcher_props}

# Apply book lines + recalibrate (operates on dicts in-place)
attach_book_lines(pitcher_props)
recalibrate_props_with_book_lines(pitcher_props)

# Patch each prop that changed
changed = 0
unchanged = 0
demoted_from_strong_to_skip = 0
for p in pitcher_props:
    pid = p['id']
    before = pre[pid]
    after_payload = {}
    for field in ('tier', 'conviction', 'prop_line', 'book_line', 'book_over_odds',
                  'book_under_odds', 'book_source', 'signals'):
        if field in p:
            after_payload[field] = p.get(field)
    # Skip if nothing materially changed (defensive — avoid no-op PATCHes)
    if (before['tier'] == p.get('tier') and before['conviction'] == p.get('conviction')
            and before['book_line'] == p.get('book_line')):
        unchanged += 1
        continue
    status = patch_prop(pid, after_payload)
    if status in (200, 204):
        changed += 1
        bl = p.get('book_line')
        edge = (p.get('signals') or {}).get('_edge_at_book')
        bef_tier = before['tier']; aft_tier = p.get('tier')
        if bef_tier in ('PRIME', 'STRONG') and aft_tier == 'SKIP':
            demoted_from_strong_to_skip += 1
        print(f"  [{bef_tier}→{aft_tier}] {p.get('player_name')[:24]:24s} {p['prop_type']:10s} "
              f"line {before['prop_line']}→{p.get('prop_line')} book={bl} edge={edge} "
              f"conv {before['conviction']}→{p.get('conviction')}")
    else:
        print(f'  PATCH FAILED status={status} prop_id={pid}')

print()
print(f'Summary: {changed} props updated, {unchanged} unchanged, {demoted_from_strong_to_skip} demoted PRIME/STRONG → SKIP')
