"""Seed NCAAF canonical aliases discovered by audit_team_alias_gaps.py.

Two classes of fixes:
  A. New canonicals: teams missing from ncaaf_team_aliases entirely
     (West Georgia, UT Rio Grande Valley, Virginia Tech, University at
      Albany, Texas A&M as separate row).
  B. Alt-name adds: canonical exists but raw source variant not in
     alt_names (VMI Keydets → VMI, Virginia Tech Hokies → Virginia
     Tech, Texas A&M Aggies → Texas A&M).

After this runs, resolver hits on those raw names cleanly.

USAGE:
  python _seed_ncaaf_missing_aliases_2026_09_03.py --dry-run
  python _seed_ncaaf_missing_aliases_2026_09_03.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
K = os.environ['SUPABASE_KEY']
H_READ = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


# Class A: brand-new canonical rows (team missing entirely)
NEW_CANONICALS = [
    {'canonical_name': 'West Georgia', 'location': 'West Georgia', 'nickname': 'Wolves',
     'full_name': 'West Georgia Wolves', 'classification': 'FCS',
     'alt_names': ['West Georgia', 'West Georgia Wolves', 'Wolves']},
    {'canonical_name': 'UT Rio Grande Valley', 'location': 'UT Rio Grande Valley',
     'nickname': 'Vaqueros', 'full_name': 'UT Rio Grande Valley Vaqueros',
     'classification': 'FCS',
     'alt_names': ['UT Rio Grande Valley', 'UTRGV', 'Rio Grande Valley', 'Vaqueros']},
    {'canonical_name': 'Virginia Tech', 'location': 'Virginia Tech', 'nickname': 'Hokies',
     'full_name': 'Virginia Tech Hokies', 'classification': 'FBS', 'conference': 'ACC',
     'alt_names': ['Virginia Tech', 'Virginia Tech Hokies', 'VT', 'Hokies']},
    {'canonical_name': 'Albany', 'location': 'Albany', 'nickname': 'Great Danes',
     'full_name': 'Albany Great Danes', 'classification': 'FCS',
     'alt_names': ['Albany', 'University at Albany', 'UAlbany', 'Great Danes']},
    {'canonical_name': 'Texas A&M', 'location': 'Texas A&M', 'nickname': 'Aggies',
     'full_name': 'Texas A&M Aggies', 'classification': 'FBS', 'conference': 'SEC',
     'alt_names': ['Texas A&M', 'Texas AM', 'Texas A and M', 'TAMU', 'Aggies']},
]

# Class B: existing canonical + additional alt_names to append
# Format: (canonical_name, [new alt_names to add])
ALT_ADDITIONS = [
    ('VMI', ['VMI Keydets', 'Keydets']),
    ('Wyoming', ['Wyoming', 'Wyoming Cowboys']),
    ('Youngstown State', ['Youngstown State Penguins', 'Penguins']),
    ('Utah Tech', ['Utah Tech Trailblazers', 'Trailblazers']),
    ('Tarleton State', ['Tarleton State Texans', 'Tarleton Texans']),
]


def _write_new_canonical(row: dict, dry: bool) -> str:
    if dry: return 'DRY'
    r = requests.post(f'{SB}/rest/v1/ncaaf_team_aliases',
                      headers=H_WRITE, json=row, timeout=15)
    return 'OK' if r.status_code in (200, 201, 204) else f'{r.status_code}:{r.text[:80]}'


def _append_alts(canonical: str, new_alts: list[str], dry: bool) -> str:
    # Fetch current row
    from urllib.parse import quote
    r = requests.get(f'{SB}/rest/v1/ncaaf_team_aliases',
                     params={'canonical_name': f'eq.{canonical}',
                             'select': 'canonical_name,alt_names'},
                     headers=H_READ, timeout=15)
    rows = r.json() if r.status_code == 200 else []
    if not rows: return 'MISSING_CANONICAL'
    existing = rows[0].get('alt_names') or []
    if not isinstance(existing, list): existing = []
    merged = list({*existing, *new_alts})
    if set(merged) == set(existing): return 'ALREADY_HAS'
    if dry: return f'DRY (would add {sorted(set(merged) - set(existing))})'
    r = requests.patch(
        f'{SB}/rest/v1/ncaaf_team_aliases?canonical_name=eq.{quote(canonical)}',
        headers=H_WRITE, json={'alt_names': merged}, timeout=15,
    )
    return 'OK' if r.status_code in (200, 204) else f'{r.status_code}:{r.text[:80]}'


def main(dry: bool):
    print('== Seeding NCAAF missing aliases ==')
    print(f'\n[Class A] {len(NEW_CANONICALS)} new canonicals')
    for row in NEW_CANONICALS:
        result = _write_new_canonical(row, dry)
        print(f'  {row["canonical_name"]:30s} {result}')

    print(f'\n[Class B] {len(ALT_ADDITIONS)} alt_name appends')
    for canonical, alts in ALT_ADDITIONS:
        result = _append_alts(canonical, alts, dry)
        print(f'  {canonical:30s} {result}')

    print('\nDone. Run audit_team_alias_gaps.py again to see remaining gaps.')
    print('If new canonicals + alts resolved the scraped names, mark gaps resolved:')
    print("  UPDATE team_alias_gaps SET resolved=true WHERE raw_name IN (...);")
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    sys.exit(main(dry=ap.parse_args().dry_run))
