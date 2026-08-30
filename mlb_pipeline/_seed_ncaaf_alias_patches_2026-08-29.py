#!/usr/bin/env python3
"""Patch ncaaf_team_aliases for known name-conflict cases (2026-08-29).

CFBD's alternateNames is thin. Real-world scraper output includes
variants CFBD doesn't list. Each patch here comes from an observed
resolver miss during testing.

Idempotent — safe to re-run. Merges into existing alt_names array.

RUN:
  python _seed_ncaaf_alias_patches_2026-08-29.py
"""
from __future__ import annotations
import os, sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests
SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
H  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


# canonical → extra alt_names to add
PATCHES = {
    # Ole Miss: sportsbooks + externals interchangeably use "Mississippi"
    'Ole Miss':  ['Mississippi', 'Ole Miss Rebels'],
    # NC State: some sources spell full "North Carolina State"
    'NC State':  ['North Carolina State', 'North Carolina St', 'NC State Wolfpack'],
    # UCF: full "Central Florida" pre-2007 name, still used by some sources
    'UCF':       ['Central Florida', 'UCF Knights'],
    # SMU: "Southern Methodist"
    'SMU':       ['Southern Methodist', 'Southern Methodist Mustangs'],
    # BYU: "Brigham Young"
    'BYU':       ['Brigham Young', 'Brigham Young Cougars'],
    # LSU: full "Louisiana State"
    'LSU':       ['Louisiana State', 'Louisiana State Tigers'],
    # TCU: full name Texas Christian
    'TCU':       ['Texas Christian', 'Texas Christian Horned Frogs'],
    # USC: variant "Southern California" (vs South Carolina)
    'USC':       ['Southern California', 'Southern California Trojans', 'USC Trojans'],
    # Miami (FL): sources use "Miami (FL)" to disambiguate from Miami (OH)
    'Miami':     ['Miami (FL)', 'Miami Florida', 'Miami FL', 'Miami Hurricanes'],
    # Pitt
    'Pittsburgh': ['Pitt', 'Pitt Panthers'],
    # UConn
    'Connecticut': ['UConn', 'UConn Huskies'],
    # UMass
    'Massachusetts': ['UMass', 'UMass Minutemen'],
    # UNLV
    'UNLV':      ['Nevada Las Vegas', 'Nevada-Las Vegas'],
    # FIU
    'Florida International': ['FIU', 'FIU Panthers'],
    # FAU
    'Florida Atlantic': ['FAU', 'FAU Owls'],
    # UAB
    'UAB':       ['Alabama Birmingham', 'Alabama-Birmingham'],
    # UTEP
    'UTEP':      ['Texas El Paso', 'Texas-El Paso'],
    # UTSA
    'UT San Antonio': ['UTSA', 'Texas San Antonio', 'Texas-San Antonio', 'UTSA Roadrunners'],
    # ULM
    'Louisiana Monroe': ['ULM', 'Louisiana-Monroe', 'ULM Warhawks'],
    # ULL (Louisiana)
    'Louisiana': ['Louisiana Lafayette', 'Louisiana-Lafayette', 'UL Lafayette', 'ULL', 'Ragin Cajuns'],
    # Miami (OH) — RedHawks
    'Miami (OH)': ['Miami OH', 'Miami Ohio', 'Miami-Ohio', 'Miami (Ohio)', 'RedHawks'],
    # App State
    'Appalachian State': ['App State', 'Appalachian St', 'App State Mountaineers'],
    # Georgia Southern
    'Georgia Southern': ['Ga Southern', 'Georgia Southern Eagles'],
    # Coastal Carolina
    'Coastal Carolina': ['Coastal', 'Chanticleers'],
    # South Alabama
    'South Alabama': ['South Ala', 'South Alabama Jaguars'],
}


def main():
    print(f'=== NCAAF alias patches · {len(PATCHES)} teams ===')
    # Fetch current alt_names for each canonical to merge
    ok = fail = 0
    for canon, new_alts in PATCHES.items():
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_team_aliases?canonical_name=eq.{canon}&select=alt_names',
            headers=H, timeout=10,
        )
        if r.status_code != 200 or not r.json():
            print(f'  ⚠ {canon}: not in DB (skip)')
            fail += 1
            continue
        current = r.json()[0].get('alt_names') or []
        if not isinstance(current, list): current = []
        merged = sorted(set(current) | set(new_alts))
        added = set(merged) - set(current)
        if not added:
            print(f'  · {canon}: no change (already has all)')
            ok += 1
            continue
        pr = requests.patch(
            f'{SB}/rest/v1/ncaaf_team_aliases?canonical_name=eq.{canon}',
            headers={**HW, 'Prefer': 'return=minimal'},
            json={'alt_names': merged}, timeout=10,
        )
        if pr.status_code in (200, 204):
            ok += 1
            print(f'  ✓ {canon}: +{len(added)} aliases → {sorted(added)}')
        else:
            fail += 1
            print(f'  ✗ {canon}: PATCH {pr.status_code} {pr.text[:120]}')
    print(f'\n  ok={ok} fail={fail}')


if __name__ == '__main__':
    main()
