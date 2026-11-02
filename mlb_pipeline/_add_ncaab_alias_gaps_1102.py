"""Add the three NCAAB spellings nothing in the alias table covers.

== 2026-11-02 ==============================================================
ncaab_team_resolver --audit, over every live name in both tables:

    line_history NCAAB    150 distinct names   0 unresolved
    ncaab_game_context    244 distinct names   3 unresolved
        1x 'Loyola (Chi) Ramblers'
        1x 'UMKC Kangaroos'
        1x 'Boston Univ. Terriers'

Each is a real spelling a feed emits, and each has an existing canonical row
that it plainly belongs to. The fix goes in ncaab_team_aliases because that
table is the project's permanent lookup -- ncaab_enrich_aliases.py: "The fix
is a PERMANENT lookup table -- not runtime fuzzy matching." Putting these in
a constant inside the resolver would mean the next module that resolves a
name still fails on them.

EACH TARGET WAS VERIFIED BY QUERY FIRST, not from recall, because two of the
three sit next to a team they could be confused with:

    'Loyola (Chi) Ramblers'  -> Loyola Chicago     espn 2350
         the table also holds Loyola Marymount (2351) and Loyola MD (2352),
         so 'Loyola' alone would be ambiguous. '(Chi)' is what disambiguates
         it, and 'Ramblers' already maps to Loyola Chicago alone.
    'UMKC Kangaroos'         -> Kansas City        espn 140
         the school rebranded from UMKC; the table has it under the new name
         with mascot 'Roos'. No row contains 'UMKC' at all today.
    'Boston Univ. Terriers'  -> Boston University   espn 104
         distinct from Boston College (103). 'Terriers' already maps to
         Boston University alone; 'boston univ' cannot collide with
         'boston college'.

ADDITIVE AND IDEMPOTENT. alt_names is UNIONed, never replaced -- the array
already carries the forms earlier work derived, and overwriting it would
silently drop them. Re-running changes nothing. Every write is read back and
the resolver is re-audited at the end.

  python _add_ncaab_alias_gaps_1102.py
  python _add_ncaab_alias_gaps_1102.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

for _l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}
H_W = dict(H, **{'Content-Type': 'application/json',
                 'Prefer': 'return=representation'})

sys.path.insert(0, str(Path(__file__).parent))
import ncaab_team_resolver as R                                # noqa: E402

#: canonical_name -> spellings to add. The canonical is asserted to exist
#: and to be the ONLY row matching, before anything is written.
ADDITIONS = {
    'Loyola Chicago': ['Loyola (Chi)', 'Loyola (Chi) Ramblers'],
    'Kansas City': ['UMKC', 'UMKC Kangaroos', 'Kangaroos'],
    'Boston University': ['Boston Univ.', 'Boston Univ. Terriers'],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=== add NCAAB alias gaps - %s ===\n'
          % ('APPLY' if a.apply else 'DRY RUN'))

    r = requests.get(f'{SB}/rest/v1/ncaab_team_aliases', headers=H,
                     params={'select': 'canonical_name,odds_api_name,espn_id,'
                                       'alt_names', 'limit': 1000},
                     timeout=60)
    if r.status_code != 200:
        print(f'  !! HTTP {r.status_code} {r.text[:200]}')
        return 1
    rows = r.json()
    if not rows:
        print('  !! 0 alias rows - WIRING ERROR, not an empty table.')
        return 1
    print(f'alias rows: {len(rows)}')
    by_canon = {}
    for row in rows:
        by_canon.setdefault(row['canonical_name'], []).append(row)

    plans = []
    for canon, adds in ADDITIONS.items():
        hits = by_canon.get(canon) or []
        if len(hits) != 1:
            print(f'  x {canon!r}: expected exactly 1 row, found {len(hits)} '
                  f'- REFUSING (an ambiguous target is how a line lands on '
                  f'the wrong team)')
            return 1
        row = hits[0]
        have = list(row.get('alt_names') or [])
        have_norm = {R.normalize(x) for x in have}
        new = [x for x in adds if R.normalize(x) not in have_norm]
        print(f'\n  {canon!r}  espn={row.get("espn_id")}  '
              f'odds={row.get("odds_api_name")!r}')
        print(f'     alt_names now ({len(have)}): {have}')
        if not new:
            print('     nothing to add - already covered')
            continue
        # A spelling must not already resolve to a DIFFERENT team.
        clash = []
        for x in new:
            got = R.resolve(x)
            if got and got != canon:
                clash.append((x, got))
        if clash:
            print(f'     x REFUSING: {clash} already resolve elsewhere')
            return 1
        print(f'     adding ({len(new)}): {new}')
        plans.append((canon, sorted(set(have) | set(new)), new))

    if not plans:
        print('\n  nothing to do.')
        return 0
    if not a.apply:
        print('\n  DRY RUN - nothing written. Re-run with --apply.')
        return 0

    print('\n--- applying ---')
    bad = 0
    for canon, merged, new in plans:
        resp = requests.patch(
            f'{SB}/rest/v1/ncaab_team_aliases', headers=H_W,
            params={'canonical_name': f'eq.{canon}'},
            json={'alt_names': merged}, timeout=60)
        back = requests.get(
            f'{SB}/rest/v1/ncaab_team_aliases', headers=H,
            params={'select': 'alt_names',
                    'canonical_name': f'eq.{canon}'}, timeout=60)
        got = (back.json()[0].get('alt_names') or []) if (
            back.status_code == 200 and back.json()) else []
        got_norm = {R.normalize(x) for x in got}
        ok = all(R.normalize(x) in got_norm for x in new)
        bad += 0 if ok else 1
        print(f'   {"OK" if ok else "x "} {canon!r} HTTP {resp.status_code} '
              f'alt_names now {len(got)} entries')
        if not ok:
            print(f'      readback missing {new}; stored={got}')

    print('\n--- re-auditing the resolver against live names ---')
    R._LOADED = False
    rc = R._audit()
    return 1 if (bad or rc) else 0


if __name__ == '__main__':
    sys.exit(main())
