"""NCAAF game_context dedup — launch blocker fix.

Two duplication patterns found in the 9/4-9/13 slate:

  1. MASCOT-SUFFIX dupes: "Eastern Illinois" and "Eastern Illinois
     Panthers" ingested as separate games. Odds API returns the raw
     name; team_resolver.resolve_ncaaf_team returns the raw form back
     when it can't find an alias match. Both get canonicalized into
     game_ids that differ, so on-conflict-game_id doesn't dedup them.

  2. DATE-DRIFT dupes: Same teams on adjacent dates (BYU 9/5 vs 9/6,
     Utah 9/12 vs 9/13). Cross-timezone kickoff — game_date parsed
     from commence_time_UTC lands on the "wrong" ET calendar day.

For each dupe group we keep the row with the shortest/canonical
team-name form (matches team_resolver.resolve_ncaaf_team output for
future re-ingest), delete the other row, and print the raw→canonical
mapping so it can be seeded into ncaaf_team_aliases.

Runs on ncaaf_game_context (has picks + primary_play) then ncaaf_game_results
(source of truth for scores). Cascades: deleting a game_context row will
also cascade to any picks/reads tied to that game_id.

Usage:
    python ncaaf_dedup_2026_09_04.py --dry-run       # preview
    python ncaaf_dedup_2026_09_04.py                 # execute
    python ncaaf_dedup_2026_09_04.py --add-aliases   # also seed missing aliases
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

import requests

_env = Path(__file__).parent / '.env'
for line in _env.read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# Common NCAAF mascot suffixes to strip when detecting mascot-dupes
MASCOT_SUFFIXES = [
    'Tigers', 'Bulldogs', 'Panthers', 'Wildcats', 'Eagles', 'Cardinals',
    'Bears', 'Lions', 'Aggies', 'Hornets', 'Vandals', 'Sycamores',
    'Buccaneers', 'Redhawks', 'Demons', 'Colonels', 'Jackrabbits',
    'Wolves', 'Warriors', 'Owls', 'Rams', 'Bulls', 'Cougars', 'Hawks',
    'Huskies', 'Falcons', 'Broncos', 'Mustangs', 'Bison', 'Delta Devils',
    'Governors', 'Chanticleers', 'Rattlers', 'Blazers', 'Racers',
    'Golden Panthers', 'Silverswords', 'Cyclones', 'Beavers',
    'Sooners', 'Longhorns', 'Aztecs', 'Bruins', 'Trojans', 'Ducks',
    'Golden Bears', 'Cowboys', 'Cavaliers', 'Utes', 'Volunteers',
    'Gators', 'Seminoles', 'Hurricanes', 'Yellow Jackets', 'Rebels',
    'Fighting Irish', 'Fighting Illini', 'Nittany Lions', 'Buckeyes',
    'Wolverines', 'Spartans', 'Hoosiers', 'Boilermakers', 'Terrapins',
    'Scarlet Knights', 'Golden Gophers', 'Fighting Sioux',
    'Gaels', 'Retrievers', 'Anteaters', 'Bulldogs', 'Explorers',
    'Cardinal', 'Golden Flashes', 'Musketeers', 'Wildcats',
    # 9/4 audit adds:
    'Gold',   # Arkansas-Pine Bluff "Gold" (the actual mascot)
    'Panther', 'Aggie',  # partial-plural variants seen today
]


def _strip_mascot(name: str) -> str:
    """Strip trailing mascot suffix if present. Returns cleaned base name."""
    if not name: return name
    n = name.strip()
    for suf in sorted(MASCOT_SUFFIXES, key=len, reverse=True):
        if n.endswith(' ' + suf):
            return n[:-len(suf) - 1].strip()
    return n


def _fingerprint(home: str, away: str, game_date: str, tz_tolerance: bool = True) -> tuple:
    """Return a fingerprint that collapses mascot-suffix + adjacent-date variants
    of the same game so both dupe types cluster together."""
    h = _strip_mascot(home).lower()
    a = _strip_mascot(away).lower()
    return (h, a, game_date)


def _adjacent_dates(a: str, b: str) -> bool:
    try:
        da = datetime.fromisoformat(a).date()
        db = datetime.fromisoformat(b).date()
        return abs((da - db).days) <= 1
    except Exception:
        return False


def find_dupes(rows: list, tz_tolerance: bool = True) -> list[list[dict]]:
    """Return list of dupe groups (each with 2+ rows)."""
    # Primary bucket: (mascot-stripped home, mascot-stripped away, date)
    bucket = defaultdict(list)
    for r in rows:
        h = _strip_mascot(r.get('home_team', '')).lower()
        a = _strip_mascot(r.get('away_team', '')).lower()
        d = r.get('game_date', '')
        bucket[(h, a, d)].append(r)

    groups = [v for v in bucket.values() if len(v) > 1]

    if not tz_tolerance:
        return groups

    # Second pass: merge (h, a, date) groups whose dates are adjacent
    by_teams = defaultdict(list)
    for k, v in bucket.items():
        h, a, d = k
        by_teams[(h, a)].append((d, v))
    for (h, a), dated in by_teams.items():
        if len(dated) < 2: continue
        # Consolidate all adjacent-date rows for the same teams
        dated.sort()
        rolling = [dated[0]]
        for entry in dated[1:]:
            if _adjacent_dates(entry[0], rolling[-1][0]):
                rolling.append(entry)
            else:
                # Not adjacent — emit the group we've built and reset
                if len(rolling) > 1 or sum(len(v) for _, v in rolling) > 1:
                    combined = [r for _, v in rolling for r in v]
                    if len(combined) > 1 and combined not in groups:
                        groups.append(combined)
                rolling = [entry]
        if len(rolling) > 1 or sum(len(v) for _, v in rolling) > 1:
            combined = [r for _, v in rolling for r in v]
            if len(combined) > 1 and combined not in groups:
                groups.append(combined)

    # Dedup identical groups
    seen = set()
    unique = []
    for g in groups:
        key = tuple(sorted(r['game_id'] for r in g))
        if key in seen: continue
        seen.add(key)
        unique.append(g)
    return unique


def pick_keeper(group: list[dict]) -> dict:
    """Choose the row to keep. Prefer:
      1. Shortest team names (canonical form, no mascot suffix)
      2. Row with the most populated fields (close_home_ml, close_spread, etc.)
      3. Newest updated_at (if available)
    """
    def _score(r):
        h_len = len(r.get('home_team', ''))
        a_len = len(r.get('away_team', ''))
        # Populated field count — presence of close_home_ml is high signal
        pop = sum(1 for k in ('close_home_ml', 'close_away_ml', 'close_spread', 'close_total')
                  if r.get(k) is not None)
        # Prefer LOWER total name length (canonical) but HIGHER populated count
        # Return tuple where SMALLER is better for name, LARGER for population
        return (-pop, h_len + a_len)
    return min(group, key=_score)


def dedup(cutoff_date: str, dry_run: bool = True, add_aliases: bool = False) -> None:
    """Dedup ncaaf_game_context rows for game_date >= cutoff_date."""
    r = requests.get(f'{SB}/rest/v1/ncaaf_game_context',
                     headers=H_READ,
                     params={'select': 'game_id,game_date,home_team,away_team,'
                                       'close_home_ml,close_away_ml,close_spread,close_total,'
                                       'updated_at',
                             'game_date': f'gte.{cutoff_date}',
                             'limit': '1000',
                             'order': 'game_date.asc,home_team.asc'},
                     timeout=20)
    rows = r.json() if r.status_code == 200 else []
    if not isinstance(rows, list):
        print(f'⛔ fetch failed: {rows}'); return
    print(f'Loaded {len(rows)} ncaaf_game_context rows since {cutoff_date}')

    groups = find_dupes(rows)
    print(f'Found {len(groups)} dupe groups\n')

    alias_ops = []
    context_deletes = []
    results_deletes = []

    for g in groups:
        keeper = pick_keeper(g)
        losers = [r for r in g if r['game_id'] != keeper['game_id']]
        print(f'  KEEP  {keeper["game_id"]}  {keeper["away_team"]:35s} @ {keeper["home_team"]:35s}  '
              f'sp={keeper.get("close_spread")}  ML={keeper.get("close_home_ml")}')
        for l in losers:
            print(f'  DROP  {l["game_id"]}  {l["away_team"]:35s} @ {l["home_team"]:35s}  '
                  f'sp={l.get("close_spread")}  ML={l.get("close_home_ml")}')
            context_deletes.append(l['game_id'])
            results_deletes.append(l['game_id'])
            if add_aliases:
                # Add raw→canonical mapping if the names differ
                if l['home_team'] != keeper['home_team']:
                    alias_ops.append((l['home_team'], keeper['home_team']))
                if l['away_team'] != keeper['away_team']:
                    alias_ops.append((l['away_team'], keeper['away_team']))
        print()

    print(f'\nSummary: {len(context_deletes)} game_context deletes, {len(alias_ops)} alias ops')
    if dry_run:
        print('\n[DRY RUN] no writes performed. Re-run without --dry-run to apply.')
        return

    # Execute deletes
    ctx_del = 0
    res_del = 0
    for gid in context_deletes:
        r = requests.delete(f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{gid}',
                            headers=H_WRITE, timeout=10)
        if r.status_code in (200, 204): ctx_del += 1
        else: print(f'  ⚠ ctx delete failed for {gid}: {r.status_code} {r.text[:150]}')
    for gid in results_deletes:
        r = requests.delete(f'{SB}/rest/v1/ncaaf_game_results?game_id=eq.{gid}',
                            headers=H_WRITE, timeout=10)
        if r.status_code in (200, 204): res_del += 1
    print(f'  Deleted {ctx_del} game_context + {res_del} game_results rows')

    # Seed aliases
    if add_aliases and alias_ops:
        seeded = 0
        for raw, canonical in alias_ops:
            row = {'alt_name': raw, 'canonical': canonical,
                   'source': 'ncaaf_dedup_2026_09_04'}
            r = requests.post(f'{SB}/rest/v1/ncaaf_team_aliases',
                              headers=H_WRITE, json=row, timeout=10)
            if r.status_code < 300: seeded += 1
        print(f'  Seeded {seeded}/{len(alias_ops)} ncaaf_team_aliases rows')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cutoff', default='2026-09-04',
                    help='Only dedup games on or after this date (default: today).')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--add-aliases', action='store_true',
                    help='Also seed raw→canonical mappings into ncaaf_team_aliases.')
    args = ap.parse_args()
    dedup(args.cutoff, dry_run=args.dry_run, add_aliases=args.add_aliases)


if __name__ == '__main__':
    main()
