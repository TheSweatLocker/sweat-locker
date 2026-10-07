#!/usr/bin/env python3
"""Update ONLY `yesterday_recap` on today's cached Sweat Card.

WHY (2026-10-07)
----------------
Andy: "what does yesterday recap sweat card still not reflect correct record?"

Two things were wrong and both are now fixed in generate_sweat_card, but the
fix could not reach the app today:

  * the recap read stale results — `_resolved_result` returned early on any
    stored non-Pending value, so "Yamamoto Over 17.5 Outs = Loss" was frozen
    into the card even though the box score says 21 outs, and the fallback
    resolved against mlb_pipeline_props, where Yastrzemski's row no longer
    exists at all. It now consults public_receipts first.
  * the card HARD-LOCKS at noon ET and refuses every republish after it.

The lock is right: it stops today's PICKS changing under a subscriber who has
already acted on them. It is not meant to freeze yesterday's RESULTS, which
settle hours after the lock by definition — games finish at night and grade in
the morning. With no way to refresh them, a wrong or pending grade stayed on
the home screen for a full day.

So this writes exactly one key. It never touches top_8, potd, dawg, the picks,
or anything else in the payload — it recomputes `yesterday_recap` via the same
generate_sweat_card.fetch_yesterday_recap() the card itself uses, and patches
that single field. Nothing it writes can alter a published pick.

    python refresh_yesterday_recap.py            # show the diff
    python refresh_yesterday_recap.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}


def _summary(recap):
    s = recap.get('top_8_summary') or {}
    return (f"{s.get('wins', 0)}-{s.get('losses', 0)}"
            + (f"-{s.get('pushes')}" if s.get('pushes') else '')
            + (f"  ({s.get('pending')} pending)" if s.get('pending') else ''))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--date', default=None, help='card date (ET), default today')
    args = ap.parse_args()

    import generate_sweat_card as G
    today = args.date or G.today_et()
    key = f'sweat_card_{today}'
    print(f'=== refresh_yesterday_recap · {key} · '
          f'{"APPLY" if args.apply else "DRY"} ===\n')

    rows = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H, timeout=60,
                        params={'select': 'cache_key,data',
                                'cache_key': f'eq.{key}'}).json()
    if not rows:
        print(f'  no cached card for {today} — nothing to refresh')
        return 0
    data = rows[0].get('data')
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        print('  cached card payload is not an object — refusing')
        return 1

    old = data.get('yesterday_recap') or {}
    new = G.fetch_yesterday_recap()
    if not new or not new.get('top_8'):
        print('  recomputed recap has no top_8 — refusing to overwrite')
        return 1

    print(f'  stored : {_summary(old)}   potd={(old.get("potd") or {}).get("result")}')
    print(f'  fresh  : {_summary(new)}   potd={(new.get("potd") or {}).get("result")}')
    o = {str(p.get('label')): p.get('result') for p in (old.get('top_8') or [])}
    changed = [(p.get('label'), o.get(str(p.get('label'))), p.get('result'))
               for p in new['top_8']
               if o.get(str(p.get('label'))) != p.get('result')]
    print(f'\n  picks whose result changes: {len(changed)}')
    for lbl, was, now in changed:
        print(f'     {str(lbl)[:48]:48s} {was} -> {now}')
    if not changed and _summary(old) == _summary(new):
        print('\n  already current — nothing to write')
        return 0
    if not args.apply:
        print('\n  re-run with --apply to write (only yesterday_recap changes)')
        return 0

    data['yesterday_recap'] = new
    r = requests.patch(f'{SB}/rest/v1/jerry_cache', headers=H_W, timeout=60,
                       params={'cache_key': f'eq.{key}'},
                       data=json.dumps({'data': data}))
    back = r.json() if r.content else []
    ok = False
    if r.status_code in (200, 204) and back:
        got = back[0].get('data')
        if isinstance(got, str):
            got = json.loads(got)
        ok = ((got or {}).get('yesterday_recap', {}).get('top_8_summary')
              == new.get('top_8_summary'))
    print(f'\n  {"written and verified by read-back" if ok else f"FAILED {r.status_code}: {r.text[:200]}"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
