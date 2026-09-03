"""Cleanup one-off: mark historical team_alias_gaps rows as resolved
where the raw name is a known non-CFB pro-sport team or a page-title
noise string. Preserves the row for audit; just flips resolved=true so
the daily audit filter (--min-hits 2) doesn't keep surfacing them.

Also marks resolved the ~10 aliases seeded by
_seed_ncaaf_missing_aliases_2026_09_03.py since those now cleanly
resolve.

USAGE:
  python _cleanup_alias_gap_pollution_903.py --dry-run
  python _cleanup_alias_gap_pollution_903.py
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def main(dry: bool):
    from team_resolver import _OTHER_SPORT_TEAMS, _norm, resolve_ncaaf_team, clear_cache
    clear_cache()

    r = requests.get(
        f'{SB}/rest/v1/team_alias_gaps',
        params={'sport': 'eq.NCAAF', 'resolved': 'is.false',
                'select': 'sport,source,raw_name'},
        headers=H_READ, timeout=30,
    )
    rows = r.json() if r.status_code == 200 else []
    print(f'Unresolved NCAAF gaps: {len(rows)}')

    from team_resolver import _is_probable_team_name
    to_resolve = []
    for row in rows:
        raw = row.get('raw_name') or ''
        if not raw: continue
        n = _norm(raw)
        # (a) known other-sport pollution
        if n in _OTHER_SPORT_TEAMS:
            to_resolve.append((raw, row.get('source'), 'other_sport'))
            continue
        # (b) resolves now (post-seed / post-cache-fix)
        if resolve_ncaaf_team(raw) is not None:
            to_resolve.append((raw, row.get('source'), 'now_resolves'))
            continue
        # (c) scraper junk — page titles, sentence fragments
        if not _is_probable_team_name(raw):
            to_resolve.append((raw, row.get('source'), 'scraper_junk'))
            continue
    print(f'To mark resolved: {len(to_resolve)}')
    for raw, src, reason in to_resolve[:15]:
        print(f'  {reason:14s} {src:15s} {raw!r}')

    if dry:
        print('\n[DRY] no writes')
        return 0

    from urllib.parse import quote
    for raw, src, _ in to_resolve:
        try:
            requests.patch(
                f'{SB}/rest/v1/team_alias_gaps'
                f'?sport=eq.NCAAF&source=eq.{quote(src)}&raw_name=eq.{quote(raw)}',
                headers=H_WRITE, json={'resolved': True}, timeout=10,
            )
        except Exception as e:
            print(f'  ✗ failed {raw!r}: {e}')
    print(f'\n✓ marked {len(to_resolve)} rows resolved')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    sys.exit(main(dry=ap.parse_args().dry_run))
