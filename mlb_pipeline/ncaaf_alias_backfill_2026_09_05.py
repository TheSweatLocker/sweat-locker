"""Comprehensive NCAAF team alias backfill from CFBD.

Root cause of Washington/Rice bug (9/5): Odds API returned "Houston Baptist
Huskies @ Rice Owls" but old resolver mapped "Houston Baptist Huskies" →
Washington via bare "Huskies" nickname collision. Fixed ordering in
team_resolver today, but Houston Baptist and dozens of other FCS/D-II
teams still return None or mis-resolve because they're missing from
ncaaf_team_aliases entirely.

This script:
  1. Pulls every FBS + FCS + D-II team from CFBD /teams endpoint (~1900 rows)
  2. Upserts into ncaaf_team_aliases keyed on canonical_name.
     - INSERT new canonical rows with school/mascot/abbrev/alt_names[]
     - UPDATE existing rows by MERGING alt_names arrays (don't overwrite)
  3. Emits per-team variants:
       school (canonical)
       abbreviation
       mascot
       school + mascot (Odds API shape: "Rice Owls", "Houston Baptist Huskies")
       hyphen ↔ space transforms
       St ↔ St. transforms
       alt_names[] from CFBD
  4. Reports coverage delta

Post-backfill, resolver should recognize every FCS/G5 name variant Odds
API sends. FBS-only Google-famous names already work; this fills the
FCS/G5/D-II gap.

Usage:
  python ncaaf_alias_backfill_2026_09_05.py                # execute
  python ncaaf_alias_backfill_2026_09_05.py --dry-run      # preview

Prereq: CFBD_API_KEY in .env
"""
from __future__ import annotations
import argparse, os, sys, io, requests
from pathlib import Path
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
CFBD_KEY = os.environ['CFBD_API_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}
CFBD_H = {'Authorization': f'Bearer {CFBD_KEY}'}


def _fetch_cfbd_teams() -> list[dict]:
    r = requests.get('https://api.collegefootballdata.com/teams',
                     headers=CFBD_H, timeout=30)
    if r.status_code != 200:
        print(f'  ⛔ CFBD /teams returned {r.status_code}: {r.text[:200]}')
        return []
    return r.json() or []


def _team_variants(team: dict) -> tuple[str, str, str, str, str, list[str]]:
    """Return (canonical, full_name, location, nickname, abbrev, alt_names_list)
    matching the ncaaf_team_aliases schema.
    """
    school = (team.get('school') or '').strip()
    mascot = (team.get('mascot') or '').strip()
    abbr = (team.get('abbreviation') or '').strip()
    full = f'{school} {mascot}'.strip() if mascot else school

    alts: set[str] = set()
    # Odds-API-style compound (already in full_name but keep for safety)
    if school and mascot:
        alts.add(f'{school} {mascot}')
    for alt in (team.get('alternateNames') or []):
        if alt and isinstance(alt, str):
            alts.add(alt.strip())
    # Hyphen/space swaps
    if school and '-' in school:
        alts.add(school.replace('-', ' '))
    if school and ' ' in school:
        alts.add(school.replace(' ', '-'))
    # St ↔ St. swaps (common variant)
    if school and 'St.' in school:
        alts.add(school.replace('St.', 'St'))
    if school and 'St ' in school:
        alts.add(school.replace('St ', 'St. '))
    # Drop 'the ' prefix
    if school.lower().startswith('the '):
        alts.add(school[4:])
    # Add 'the ' prefix (some FCS names use it)
    else:
        alts.add(f'The {school}')
    # Remove the canonical itself from alts (schema stores canonical separately)
    alts.discard(school)

    return (school, full, school, mascot, abbr, sorted(alts))


def _existing_by_canonical() -> dict:
    """Return {canonical_name: full existing row} for merging."""
    out = {}
    for offset in range(0, 20000, 1000):
        r = requests.get(f'{SB}/rest/v1/ncaaf_team_aliases',
                         params={'select': '*', 'limit': '1000', 'offset': str(offset)},
                         headers=H_READ, timeout=15)
        chunk = r.json() if r.status_code == 200 else []
        if not chunk: break
        for row in chunk:
            c = (row.get('canonical_name') or '').strip()
            if c: out[c] = row
        if len(chunk) < 1000: break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--classifications', default='fbs,fcs,ii',
                    help='CFBD classifications to include (comma). Default: fbs,fcs,ii')
    args = ap.parse_args()

    print('=== NCAAF alias backfill (CFBD /teams → ncaaf_team_aliases) ===\n')

    keep_classes = set(c.strip().lower() for c in args.classifications.split(','))

    existing = _existing_by_canonical()
    print(f'Existing canonical rows in DB: {len(existing)}')

    teams = _fetch_cfbd_teams()
    print(f'CFBD teams fetched: {len(teams)}')

    # Filter by classification
    filtered = [t for t in teams
                if (t.get('classification') or 'unknown').lower() in keep_classes]
    print(f'  keeping classifications {sorted(keep_classes)}: {len(filtered)} teams')

    # Build upsert payloads
    new_canon: list[dict] = []
    merged_updates: list[dict] = []
    alias_count_delta = 0

    for t in filtered:
        canon, full, loc, nick, abbr, alts = _team_variants(t)
        if not canon: continue
        if canon in existing:
            # MERGE alt_names (don't overwrite)
            existing_row = existing[canon]
            existing_alts = existing_row.get('alt_names') or []
            if not isinstance(existing_alts, list): existing_alts = []
            merged = sorted(set(existing_alts) | set(alts))
            if merged != sorted(existing_alts):
                merged_updates.append({
                    'canonical_name': canon,
                    'alt_names': merged,
                    # Also refresh mascot/abbrev if empty in DB
                    'nickname': existing_row.get('nickname') or nick,
                    'abbrev': existing_row.get('abbrev') or abbr,
                })
                alias_count_delta += (len(merged) - len(existing_alts))
        else:
            new_canon.append({
                'canonical_name': canon,
                'full_name': full,
                'location': loc,
                'nickname': nick,
                'abbrev': abbr,
                'alt_names': alts,
                'classification': (t.get('classification') or 'unknown').upper(),
                'conference': t.get('conference'),
                'division': t.get('division'),
            })

    print(f'\nNew canonical inserts: {len(new_canon)}')
    print(f'Existing rows to merge:{len(merged_updates)}  (+{alias_count_delta} alt_names added)')

    if new_canon[:10]:
        print('\n  Sample new inserts (first 10):')
        for r in new_canon[:10]:
            print(f'    {r["canonical_name"]:32s} ({r.get("classification","?"):5s}) alts={len(r.get("alt_names",[]))}')

    if merged_updates[:5]:
        print('\n  Sample merges (first 5):')
        for r in merged_updates[:5]:
            print(f'    {r["canonical_name"]:32s} now has {len(r["alt_names"])} alt_names')

    if args.dry_run:
        print(f'\n[DRY RUN] would upsert {len(new_canon) + len(merged_updates)} rows.')
        return

    # New canonicals — batch INSERT (POST)
    ok_new = fail = 0
    for i in range(0, len(new_canon), 100):
        batch = new_canon[i:i+100]
        r = requests.post(f'{SB}/rest/v1/ncaaf_team_aliases',
                          headers=H_WRITE, json=batch, timeout=30)
        if r.status_code < 300: ok_new += len(batch)
        else:
            fail += len(batch)
            print(f'  ⚠ new batch {i}-{i+len(batch)} failed: {r.status_code} {r.text[:150]}')

    # Merges — one-by-one PATCH keyed on canonical_name (safe, doesn't touch NOT NULL cols)
    ok_upd = 0
    for row in merged_updates:
        canon = row['canonical_name']
        payload = {k: v for k, v in row.items() if k != 'canonical_name'}
        r = requests.patch(f'{SB}/rest/v1/ncaaf_team_aliases?canonical_name=eq.{requests.utils.quote(canon)}',
                           headers={**H_WRITE, 'Prefer': 'return=minimal'},
                           json=payload, timeout=15)
        if r.status_code < 300: ok_upd += 1
        else:
            fail += 1
            if fail <= 5:
                print(f'  ⚠ patch {canon!r} failed: {r.status_code} {r.text[:150]}')

    print(f'\nInserted new: {ok_new}   Merged existing: {ok_upd}   Failed: {fail}')


if __name__ == '__main__':
    main()
