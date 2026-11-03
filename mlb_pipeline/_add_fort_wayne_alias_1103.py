"""Add the odds-feed spelling 'Fort Wayne Mastodons' to ncaab_team_aliases.

The panel skipped Fort Wayne Mastodons @ Duquesne because the odds feed drops
the 'Purdue' prefix. The alias row exists as canonical 'Purdue Fort Wayne' with
alt_names [Mastodons, PFW, Purdue FW, Purdue Fort Wayne, Purdue Fort Wayne
Mastodons] -- none of which is 'Fort Wayne', and mascot-stripping
'Fort Wayne Mastodons' yields 'Fort Wayne', also absent.

ncaab_team_resolver documents the fix path explicitly: gaps are closed "by
adding alt_names to the TABLE, not by a constant here". 'Fort Wayne' is a
location-only form and no other D-I school carries it, so it cannot collide
the way a bare mascot does.

Readback-verified (feedback_204_is_not_a_write).
"""
import os
import sys
import json
from pathlib import Path

import requests

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

CANON = 'Purdue Fort Wayne'
ADD = ['Fort Wayne', 'Fort Wayne Mastodons']
APPLY = '--apply' in sys.argv


def main() -> int:
    r = requests.get(f'{SB}/rest/v1/ncaab_team_aliases', headers=H, timeout=30,
                     params={'select': 'canonical_name,alt_names',
                             'canonical_name': f'eq.{CANON}'})
    r.raise_for_status()
    rows = r.json()
    if not rows:
        print(f'!! no ncaab_team_aliases row for {CANON!r} — aborting rather '
              f'than creating one')
        return 1
    cur = list(rows[0].get('alt_names') or [])
    missing = [a for a in ADD if a not in cur]
    if not missing:
        print(f'{CANON}: alt_names already carry {ADD} — nothing to do')
        return 0
    want = sorted(set(cur) | set(ADD))
    print(f'{CANON}')
    print(f'  current alt_names: {cur}')
    print(f'  adding           : {missing}')
    if not APPLY:
        print('\n(dry run — pass --apply to write)')
        return 0

    pr = requests.patch(f'{SB}/rest/v1/ncaab_team_aliases', headers=H_W,
                        timeout=30,
                        params={'canonical_name': f'eq.{CANON}'},
                        data=json.dumps({'alt_names': want}))
    if pr.status_code not in (200, 204):
        print(f'!! PATCH {pr.status_code}: {pr.text[:200]}')
        return 1

    # Independent readback — a 204 is not a write.
    rb = requests.get(f'{SB}/rest/v1/ncaab_team_aliases', headers=H, timeout=30,
                      params={'select': 'alt_names',
                              'canonical_name': f'eq.{CANON}'})
    got = list((rb.json() or [{}])[0].get('alt_names') or [])
    still = [a for a in ADD if a not in got]
    if still:
        print(f'!! NOT CONFIRMED — readback still missing {still}')
        return 1
    print(f'  CONFIRMED: alt_names now {got}')

    # And prove the resolver actually resolves the odds spelling now.
    sys.path.insert(0, str(Path(__file__).parent))
    import ncaab_team_resolver as R
    R._LOADED = False                      # force a reload of the table
    for name in ADD:
        print(f'  resolve({name!r}) -> {R.resolve(name)!r}')
    bad = [n for n in ADD if R.resolve(n) != CANON]
    if bad:
        print(f'!! resolver still does not map {bad} to {CANON!r}')
        return 1
    print('  resolver confirmed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
