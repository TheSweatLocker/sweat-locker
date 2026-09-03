"""Round-2 NCAAF dedupe using team_resolver (not hardcoded suffix list).

The first _ncaaf_dedupe_2026_09_03.py used a mascot-suffix strip that
missed city-name variants (UMass vs Massachusetts), hyphen variants
(Arkansas-Pine Bluff vs Arkansas Pine Bluff), and suffix classes outside
its hardcoded list (Nicholls State vs Nicholls State Colonels). This
script uses resolve_ncaaf_team so canonical is authoritative — matches
what the ingest layer now writes (post 20f943c2).

Key: (game_date, resolver_canonical_away, resolver_canonical_home).
Keeps the row whose game_id most closely matches the canonical form;
deletes the rest.

USAGE:
  python _ncaaf_dedupe_resolver_2026_09_03.py --dry-run   # preview
  python _ncaaf_dedupe_resolver_2026_09_03.py             # execute
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

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
H_WRITE = {**H_READ, 'Prefer': 'return=minimal'}


def main(dry: bool):
    from team_resolver import resolve_ncaaf_team, clear_cache
    clear_cache()

    r = requests.get(
        f'{SB}/rest/v1/ncaaf_game_context',
        params={'select': 'game_id,game_date,home_team,away_team',
                'game_date': 'gte.2026-09-03'},
        headers=H_READ, timeout=30,
    )
    rows = r.json() if r.status_code == 200 else []
    print(f'{len(rows)} NCAAF rows from 9/3 onward')

    by_canonical: dict = defaultdict(list)
    for row in rows:
        ch = resolve_ncaaf_team(row.get('home_team') or '') or (row.get('home_team') or '')
        ca = resolve_ncaaf_team(row.get('away_team') or '') or (row.get('away_team') or '')
        key = (row['game_date'], ca, ch)
        by_canonical[key].append(row)

    dupes = {k: v for k, v in by_canonical.items() if len(v) > 1}
    print(f'Dupe groups (resolver canonical): {len(dupes)}')

    # Keep the row whose game_id matches the expected canonical form
    # (ncaaf_YYYYMMDD_AwayCanon_HomeCanon). Delete the rest.
    keeps, deletes = [], []
    for (dtstr, ca, ch), group in dupes.items():
        expected_gid = f'ncaaf_{dtstr.replace("-","")}_{ca}_{ch}'
        keep = None
        for row in group:
            if row['game_id'] == expected_gid:
                keep = row; break
        if keep is None:
            keep = min(group, key=lambda r: len(r['game_id']))
        for row in group:
            if row['game_id'] == keep['game_id']:
                keeps.append(row['game_id'])
            else:
                deletes.append(row['game_id'])

    print(f'Would keep: {len(keeps)}, delete: {len(deletes)}')
    for gid in deletes[:12]:
        print(f'  {"[DRY] " if dry else ""}delete {gid}')
    if dry: return 0

    for gid in deletes:
        gid_enc = quote(gid, safe='')
        # jerry_reads first (user-visible)
        requests.delete(f'{SB}/rest/v1/jerry_reads?game_id=eq.{gid_enc}',
                        headers=H_WRITE, timeout=15)
        requests.delete(f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{gid_enc}',
                        headers=H_WRITE, timeout=15)
    print(f'\nDeleted {len(deletes)} rows from ncaaf_game_context + jerry_reads')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    sys.exit(main(dry=ap.parse_args().dry_run))
