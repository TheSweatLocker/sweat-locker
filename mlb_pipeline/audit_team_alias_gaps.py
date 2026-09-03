"""Daily report of unresolved team-name aliases.

Reads team_alias_gaps and surfaces raw names the resolver couldn't map.
Run daily via cron so we see missing aliases quickly and can add them
to ncaaf_team_aliases / ncaab_team_aliases before they cause silent
external-pick drops.

Prior state: scrapers loose-matched on last-word or substring, silently
attaching wrong picks to wrong games (Ball State/New Mexico State 9/3).
2026-09-03 zero-fail refactor now DROPS picks instead of guessing —
but that creates a new failure mode: sources with unmapped names
produce ZERO picks. This audit surfaces those gaps before users notice
the missing external coverage.

USAGE:
  python audit_team_alias_gaps.py                # print report
  python audit_team_alias_gaps.py --sport NCAAF  # filter one sport
  python audit_team_alias_gaps.py --min-hits 3   # only frequently-seen gaps
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
H = {'apikey': K, 'Authorization': f'Bearer {K}'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def run(sport: str | None = None, min_hits: int = 1):
    params = {
        'select': 'sport,source,raw_name,hit_count,first_seen,last_seen,resolved',
        'resolved': 'is.false',
        'order': 'hit_count.desc,last_seen.desc',
        'limit': '500',
    }
    if sport: params['sport'] = f'eq.{sport}'

    r = requests.get(f'{SB}/rest/v1/team_alias_gaps', params=params, headers=H, timeout=30)
    rows = r.json() if r.status_code == 200 else []
    rows = [row for row in rows if (row.get('hit_count') or 0) >= min_hits]

    if not rows:
        print(f'✓ No unresolved alias gaps (sport={sport or "any"}, min_hits={min_hits})')
        return 0

    print(f'⚠ {len(rows)} unresolved team-name aliases (sport={sport or "any"}, min_hits≥{min_hits})')
    print()
    print(f'{"Sport":6}  {"Source":15}  {"Hits":>4}  {"Last seen":19}  Raw name')
    print('-' * 90)
    for row in rows[:100]:
        sp = row.get('sport') or '?'
        src = row.get('source') or '?'
        hc = row.get('hit_count') or 0
        ls = (row.get('last_seen') or '')[:19]
        raw = row.get('raw_name') or ''
        print(f'{sp:6}  {src:15}  {hc:>4}  {ls:19}  {raw}')

    print()
    print('ACTION: add aliases to ncaaf_team_aliases / ncaab_team_aliases')
    print('  Insert row with alt_names array including the raw form:')
    print('    UPDATE ncaaf_team_aliases')
    print("    SET alt_names = alt_names || '{Bethune-Cookman Wildcats}'::text[]")
    print("    WHERE canonical_name = 'Bethune-Cookman';")
    print('  Then mark the gap resolved:')
    print("    UPDATE team_alias_gaps SET resolved=true WHERE raw_name='...';")
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None, help='Filter by sport (NCAAF, NCAAB, etc)')
    ap.add_argument('--min-hits', type=int, default=1, help='Min hit_count to surface')
    args = ap.parse_args()
    sys.exit(run(sport=args.sport, min_hits=args.min_hits))
