"""One-time bootstrap: enrich ncaab_team_aliases with espn_id +
odds_api_name + robust alt_names.

WHY THIS EXISTS
---------------
The initial 365-team seed (ncaab_seed_aliases.py) only populated the
KenPom-style canonical_name + a small KNOWN_ALT_NAMES dict of tricky
cases (Saint↔St, UConn, LSU, etc.). It left odds_api_name = NULL and
espn_id = NULL for ALL rows.

Historically (pre-backend, when name mapping lived in a client-side
index) this caused unresolved name mismatches. The fix is a
PERMANENT lookup table — not runtime fuzzy matching.

This script:
  1. Pulls all D1 teams from ESPN's public teams endpoint (362 rows)
  2. For each ESPN team, finds the matching canonical_name via a
     deterministic normalizer chain (direct → St.↔Saint → State↔St. →
     ampersand handling → punctuation strip)
  3. PATCHes the matched row with:
       espn_id       = ESPN numeric ID (for future ESPN score refreshes)
       odds_api_name = ESPN displayName (Odds API uses same convention:
                       "Duke Blue Devils", "Alabama Crimson Tide")
       alt_names     = union of existing + newly derived variants
                       (mascot-only, location-only, punctuation variants)
  4. Writes an audit file `ncaab_alias_gaps.log` listing:
       - ESPN teams that couldn't map to any canonical (need manual add)
       - Alias rows with no ESPN match (probably not D1 anymore or renamed)

Idempotent — re-run refreshes and re-audits.

Usage:
    python ncaab_enrich_aliases.py
    python ncaab_enrich_aliases.py --dry-run
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

ESPN_TEAMS_URL = (
    'https://site.api.espn.com/apis/site/v2/sports/basketball/'
    'mens-college-basketball/teams?limit=500'
)


# Explicit ESPN team.location → canonical_name overrides for the ~30
# edge cases the pattern normalizer can't resolve (rebrands, colloquial
# names, unusual punctuation). PERMANENT lookup — new mismatches
# discovered in future pulls must be added here rather than caught by
# fuzzy fallback, so name drift can never silently mis-attribute.
#
# Grouped by rationale for auditability.
MANUAL_ESPN_TO_CANONICAL = {
    # Colloquial names (ESPN uses casual, KenPom uses formal)
    'UConn':                    'Connecticut',
    'Ole Miss':                 'Mississippi',
    'Pennsylvania':             'Penn',
    'Miami':                    'Miami FL',        # KenPom disambiguates from Miami OH
    'UAlbany':                  'Albany',
    'UIC':                      'Illinois Chicago',
    'App State':                'Appalachian St.',
    'Omaha':                    'Nebraska Omaha',
    'SIU Edwardsville':         'SIUE',
    'Seattle U':                'Seattle',
    'IU Indianapolis':          'IU Indy',
    'American University':      'American',

    # ESPN uses abbreviated location, KenPom uses full state name
    'UT Martin':                'Tennessee Martin',
    'UL Monroe':                'Louisiana Monroe',
    'SE Louisiana':             'Southeastern Louisiana',
    'Southeast Missouri State': 'Southeast Missouri',
    'Cal State Northridge':     'CSUN',
    'California Baptist':       'Cal Baptist',
    'Florida International':    'FIU',
    'Long Island University':   'LIU',
    'Sam Houston':              'Sam Houston St.',
    'Grambling':                'Grambling St.',
    'South Carolina Upstate':   'USC Upstate',

    # Punctuation / hyphen differences
    'NC State':                 'N.C. State',
    'Loyola Maryland':          'Loyola MD',
    'Bethune-Cookman':          'Bethune Cookman',
    'Arkansas-Pine Bluff':      'Arkansas Pine Bluff',
    'Gardner-Webb':             'Gardner Webb',
    'Texas A&M-Corpus Christi': 'Texas A&M Corpus Chris',
    'St. Thomas-Minnesota':     'St. Thomas',
    'San José State':           'San Jose St.',
}


# ─── name normalization ───────────────────────────────────────────

_MASCOT_SUFFIXES = None  # populated at runtime from ESPN team.name


def _strip_punct(s: str) -> str:
    return re.sub(r'[^\w\s&]', '', s).strip()


def _normalizations(s: str) -> list:
    """Ordered list of normalization variants for `s` to try against canonical.
    Deterministic — first match wins. Never returns duplicates."""
    if not s: return []
    variants = []
    seen = set()
    def add(x):
        if x and x not in seen:
            variants.append(x); seen.add(x)

    add(s)
    add(s.strip())
    # State ↔ St. — KenPom uses "St." (with period)
    add(re.sub(r'\bState\b', 'St.', s))
    add(re.sub(r'\bSt\.\b', 'State', s))
    add(re.sub(r'\bSt\b', 'State', s))
    # Saint ↔ St. — KenPom uses "Saint"
    add(re.sub(r'\bSt\.\s+', 'Saint ', s))
    add(re.sub(r'\bSt\s+', 'Saint ', s))
    add(re.sub(r'\bSaint\s+', 'St. ', s))
    # Ampersand
    add(s.replace('&', 'and'))
    add(s.replace(' and ', ' & '))
    # Punctuation strip
    add(_strip_punct(s))
    # Combined: State→St. + punctuation
    add(_strip_punct(re.sub(r'\bState\b', 'St.', s)))
    return variants


def match_canonical(espn_location: str, canonicals: set) -> Optional[str]:
    """Try to map ESPN `team.location` to a canonical_name in the alias table.
    Order (deterministic — no fuzzy-distance guessing):
      1. MANUAL_ESPN_TO_CANONICAL explicit override (for known edge cases)
      2. Normalization variants (State↔St., Saint↔St., ampersand, punctuation)
    Returns None if nothing matches — caller writes to gap audit."""
    override = MANUAL_ESPN_TO_CANONICAL.get(espn_location)
    if override and override in canonicals:
        return override
    for v in _normalizations(espn_location):
        if v in canonicals:
            return v
    return None


# ─── DB layer ─────────────────────────────────────────────────────

def load_aliases() -> list:
    """All 365 alias rows."""
    r = requests.get(
        f'{SB}/rest/v1/ncaab_team_aliases'
        '?select=canonical_name,kenpom_name,odds_api_name,alt_names,espn_id,conference'
        '&limit=500',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ alias fetch failed: {r.status_code} {r.text[:200]}')
        return []
    return r.json() or []


def fetch_espn_teams() -> list:
    """All D1 mens basketball teams from ESPN."""
    r = requests.get(ESPN_TEAMS_URL, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
    r.raise_for_status()
    data = r.json()
    sports = data.get('sports', [])
    leagues = (sports[0] or {}).get('leagues', []) if sports else []
    teams_wrap = (leagues[0] or {}).get('teams', []) if leagues else []
    return [t.get('team') for t in teams_wrap if t.get('team')]


def build_enriched_alt_names(existing: list, espn_team: dict) -> list:
    """Merge existing alt_names with newly derived variants from ESPN.
    Adds: displayName, name (mascot alone), short_display, abbreviation,
    and normalization variants of location."""
    alts = set(existing or [])
    display = espn_team.get('displayName')
    short_display = espn_team.get('shortDisplayName')
    location = espn_team.get('location') or ''
    mascot = espn_team.get('name') or ''
    abbr = espn_team.get('abbreviation') or ''

    if display: alts.add(display)
    if short_display and short_display != location:
        alts.add(short_display)
    # location + mascot as separate items (Odds API sometimes uses either)
    if location: alts.add(location)
    if mascot:
        alts.add(mascot)
        # "Team Mascot" reversed variant
        if location:
            alts.add(f'{location} {mascot}')
    if abbr and len(abbr) <= 4:
        alts.add(abbr)
    # Add State↔St. + Saint↔St. variants of location so future
    # third-party pulls (Covers, VSiN, Barttorvik) match without extra passes
    for v in _normalizations(location):
        if v: alts.add(v)

    # Remove canonical itself (implicit)
    return sorted(a for a in alts if a)


def patch_alias(canonical: str, payload: dict, dry_run: bool = False) -> bool:
    if dry_run:
        print(f'  [DRY] PATCH {canonical}: espn_id={payload.get("espn_id")} '
              f'odds={payload.get("odds_api_name")} '
              f'alts+={len(payload.get("alt_names") or [])}')
        return True
    r = requests.patch(
        f'{SB}/rest/v1/ncaab_team_aliases?canonical_name=eq.{requests.utils.quote(canonical, safe="")}',
        headers=H_WRITE, json=payload, timeout=15,
    )
    if r.status_code in (200, 201, 204):
        return True
    print(f'  ⚠ patch {canonical} failed {r.status_code}: {r.text[:200]}')
    return False


def write_gap_log(unmatched_espn: list, alias_no_espn: list, out_path: Path) -> None:
    """Structured audit output — grep-able + JSON-parseable per line."""
    lines = []
    lines.append(f'# ncaab_alias_gaps — {datetime.now(timezone.utc).isoformat()}')
    lines.append(f'# ESPN teams with no canonical match: {len(unmatched_espn)}')
    lines.append(f'# Alias rows with no ESPN match:     {len(alias_no_espn)}')
    lines.append('')
    lines.append('## ESPN teams needing manual mapping (add to ncaab_team_aliases)')
    for t in unmatched_espn:
        lines.append(json.dumps({
            'espn_id': t.get('id'),
            'location': t.get('location'),
            'displayName': t.get('displayName'),
            'abbreviation': t.get('abbreviation'),
            'nickname': t.get('name'),
        }))
    lines.append('')
    lines.append('## Alias rows with NO ESPN match (may be non-D1 or renamed)')
    for a in alias_no_espn:
        lines.append(json.dumps({
            'canonical_name': a.get('canonical_name'),
            'conference': a.get('conference'),
        }))
    out_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'  → wrote gap audit to {out_path}')


# ─── main ─────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    print('=== NCAAB alias enrichment ===')
    aliases = load_aliases()
    if not aliases:
        print('  ✗ ncaab_team_aliases empty — run ncaab_seed_aliases.py first'); return
    print(f'  loaded {len(aliases)} alias rows')

    espn_teams = fetch_espn_teams()
    print(f'  loaded {len(espn_teams)} ESPN D1 teams')

    canonicals = {a['canonical_name'] for a in aliases}
    alias_by_canonical = {a['canonical_name']: a for a in aliases}

    matched, unmatched = [], []
    for t in espn_teams:
        loc = t.get('location') or ''
        canonical = match_canonical(loc, canonicals)
        if canonical is None:
            unmatched.append(t)
            continue
        matched.append((canonical, t))

    print(f'\n  matched: {len(matched)}/{len(espn_teams)} ESPN teams')
    if unmatched:
        print(f'  UNMATCHED {len(unmatched)}:')
        for t in unmatched[:15]:
            print(f'    espn={t.get("id")} loc="{t.get("location")}" disp="{t.get("displayName")}"')
        if len(unmatched) > 15:
            print(f'    ... {len(unmatched)-15} more')

    # Also flag aliases with NO ESPN match
    matched_canonicals = {c for c, _ in matched}
    alias_no_espn = [a for a in aliases if a['canonical_name'] not in matched_canonicals]
    if alias_no_espn:
        print(f'\n  {len(alias_no_espn)} alias rows had NO ESPN match:')
        for a in alias_no_espn[:15]:
            print(f'    canonical="{a["canonical_name"]}" ({a.get("conference")})')

    # Apply patches
    patched = 0
    for canonical, t in matched:
        existing_alts = alias_by_canonical[canonical].get('alt_names') or []
        payload = {
            'espn_id': str(t.get('id')) if t.get('id') else None,
            'odds_api_name': t.get('displayName'),
            'alt_names': build_enriched_alt_names(existing_alts, t),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        if patch_alias(canonical, payload, dry_run=dry_run):
            patched += 1

    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}patched {patched}/{len(matched)} alias rows')

    # Write structured gap audit
    gap_path = Path(__file__).parent / 'ncaab_alias_gaps.log'
    write_gap_log(unmatched, alias_no_espn, gap_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
