"""CFBD → ncaaf_team_aliases sync (2026-08-29).

Nightly refresher for the team-alias truth table. Pulls CFBD /teams
(1900+ teams across FBS/FCS/D2/D3) and upserts into ncaaf_team_aliases
so the resolver always has a fresh list of every real team name.

FBS/FCS get full alias rows. D2/D3 get lighter rows (canonical + full
+ nickname) since they show up rarely (FBS-vs-FCS Week 1 games) but
missing them silently drops picks.

CFBD /teams fields we use:
  school          → canonical_name (matches CFBD advanced-stats team field)
  location.name   → NOT the location field; we use `school` as location too
  mascot          → nickname
  abbreviation    → abbrev (short code like FSU, TCU)
  alternateNames  → seed alt_names JSONB
  conference      → conference
  classification  → division (FBS/FCS/D2/D3)

RUN:
  python ncaaf_alias_sync.py                # sync all
  python ncaaf_alias_sync.py --dry-run      # print what would change
  python ncaaf_alias_sync.py --fbs-only     # skip FCS/D2/D3
"""
from __future__ import annotations
import argparse, os, sys, json
from pathlib import Path

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB  = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
CFBD = os.environ.get('CFBD_API_KEY') or ''
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
     'Content-Type': 'application/json',
     'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def fetch_cfbd_teams() -> list:
    r = requests.get('https://api.collegefootballdata.com/teams',
        headers={'Authorization': f'Bearer {CFBD}'}, timeout=20)
    if r.status_code != 200:
        print(f'  ✗ CFBD /teams failed {r.status_code}: {r.text[:150]}')
        return []
    return r.json() or []


def _to_row(t: dict) -> dict | None:
    """Map CFBD team → ncaaf_team_aliases row."""
    school = (t.get('school') or '').strip()
    if not school: return None
    mascot = (t.get('mascot') or '').strip()
    abbrev = (t.get('abbreviation') or '').strip()
    classification = (t.get('classification') or '').lower()
    conference = t.get('conference')

    # Full name = "School Mascot" if mascot exists, else just school
    full = f'{school} {mascot}'.strip() if mascot else school

    # Alt names: CFBD's alternateNames + a few reasonable derived variants
    alt_raw = t.get('alternateNames') or []
    alt = set(str(x).strip() for x in alt_raw if x)
    # Common Odds API pattern: "School Mascot" (already covered by full_name)
    # ESPN pattern: "School Mascot" or just School
    # Handful of manual normalizations that show up across sources:
    if abbrev: alt.add(abbrev)
    # Also common: "State" abbreviated to "St" or "St."
    if 'State' in school:
        alt.add(school.replace('State', 'St'))
        alt.add(school.replace('State', 'St.'))
    # Remove the ones we already store elsewhere
    alt.discard(school)
    alt.discard(full)
    alt.discard(mascot)

    return {
        'canonical_name': school,
        'full_name': full or school,
        'location': school,        # CFBD school = our location convention
        'nickname': mascot or None,
        'abbrev': abbrev or None,
        'conference': conference,
        'classification': classification.upper() if classification else None,
        'division': classification.upper() if classification else None,
        'alt_names': sorted(alt),
    }


def sync(dry_run: bool = False, fbs_only: bool = False) -> None:
    if not CFBD:
        print('  ✗ CFBD_API_KEY missing — abort')
        return

    teams = fetch_cfbd_teams()
    print(f'  CFBD returned {len(teams)} teams')

    # Dedupe by canonical_name — CFBD returns same school name at
    # different classification levels (e.g. "Miami" as FBS Hurricanes +
    # FBS Miami (OH) + possibly D3). on_conflict PK is canonical_name so
    # duplicates within one batch → 500. Keep the highest-tier version
    # (FBS > FCS > II > III > UNKNOWN) so search always resolves to the
    # more prominent program.
    _TIER = {'FBS': 5, 'FCS': 4, 'II': 3, 'III': 2, 'UNKNOWN': 1, None: 0}
    picked: dict[str, dict] = {}
    by_class = {}
    for t in teams:
        r = _to_row(t)
        if not r: continue
        cls = r.get('classification') or 'UNKNOWN'
        by_class[cls] = by_class.get(cls, 0) + 1
        if fbs_only and cls != 'FBS': continue
        canon = r['canonical_name']
        existing = picked.get(canon)
        if existing is None or _TIER.get(cls, 0) > _TIER.get(existing.get('classification'), 0):
            picked[canon] = r
    rows = list(picked.values())

    print(f'  breakdown: {by_class}')
    print(f'  deduped candidates: {len(rows)}')

    if dry_run:
        print(f'\n  [DRY] sample first 3 rows:')
        for r in rows[:3]: print(f'    {r}')
        return

    written = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i+200]
        r = requests.post(
            f'{SB}/rest/v1/ncaaf_team_aliases?on_conflict=canonical_name',
            headers=H, json=chunk, timeout=30,
        )
        if r.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ chunk {i} write failed {r.status_code}: {r.text[:200]}')
    print(f'  ✓ upserted {written}/{len(rows)} team aliases')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--fbs-only', action='store_true',
                    help='Skip FCS/D2/D3 (default: include all so FBS-vs-FCS games resolve)')
    args = ap.parse_args()
    print(f'=== ncaaf_alias_sync · fbs_only={args.fbs_only} · dry_run={args.dry_run} ===')
    sync(dry_run=args.dry_run, fbs_only=args.fbs_only)


if __name__ == '__main__':
    main()
