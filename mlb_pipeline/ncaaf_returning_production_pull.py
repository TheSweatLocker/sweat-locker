"""NCAAF returning-production puller (2026-08-09 · Phase 2).

Pulls CFBD's returning production % per team per season. This is the
strongest variance-reducing signal for early-season CFB games
(Weeks 1-3) when current-season EPA/SP+ hasn't accumulated.

Headline metric: `percentPPA` — fraction of last season's total
Predicted Points Added returning to the roster.

CFBD endpoint (v1, verified 2026-08-09):
  GET https://api.collegefootballdata.com/player/returning?year=YYYY

Response is a list (one row per team) with:
  season, team, conference,
  totalPPA, totalPassingPPA, totalReceivingPPA, totalRushingPPA,
  percentPPA (headline 0-1 fraction),
  percentPassingPPA, percentReceivingPPA, percentRushingPPA,
  usage, passingUsage, receivingUsage, rushingUsage

Team keying: CFBD short form matches ncaaf_team_aliases.canonical_name.
No mapping needed — pass through.

CAVEAT: CFBD's returning-production feed is OFFENSE ONLY. No defense
split. We'll use offense returning as a proxy (defenses correlate ~0.4
year-over-year with offense continuity in the same program per audits).

CLI:
  python ncaaf_returning_production_pull.py --season 2026
  python ncaaf_returning_production_pull.py --all-seasons  # 2015-current
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
CFBD_KEY = os.environ.get('CFBD_API_KEY') or os.environ.get('CFBD_KEY')
H_SB = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
        'Content-Type': 'application/json', 'Prefer': 'resolution=merge-duplicates,return=minimal'}
H_CFBD = {'Authorization': f'Bearer {CFBD_KEY}', 'Accept': 'application/json'}

CFBD_URL = 'https://api.collegefootballdata.com/player/returning'


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def fetch(season: int) -> list:
    r = requests.get(CFBD_URL, headers=H_CFBD, params={'year': season}, timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ CFBD {r.status_code}: {r.text[:200]}')
        return []
    return r.json()


def upsert(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    # Normalize keys (PostgREST batch upsert requirement)
    all_keys = set()
    for r in rows: all_keys.update(r.keys())
    normalized = [{k: r.get(k) for k in all_keys} for r in rows]
    if dry_run:
        print(f'  DRY: would upsert {len(normalized)} rows')
        for r in normalized[:5]:
            print(f'    {r["team"]:20s} season={r["season"]}  '
                  f'off_ret={r["returning_offense_pct"]}')
        return len(normalized)
    written = 0
    for i in range(0, len(normalized), 100):
        chunk = normalized[i:i+100]
        wr = requests.post(
            f'{SB}/rest/v1/ncaaf_returning_production?on_conflict=team,season',
            headers=H_SB, data=json.dumps(chunk, default=str), timeout=15)
        if wr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ chunk {i} {wr.status_code}: {wr.text[:200]}')
    return written


def run(season: int, dry_run: bool = False) -> int:
    print(f'=== NCAAF returning production · season {season} ===')
    if not CFBD_KEY:
        print('  ⛔ CFBD_API_KEY missing')
        return 0
    raw = fetch(season)
    print(f'  {len(raw)} team rows from CFBD')
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for r in raw:
        team = r.get('team')
        if not team: continue
        rows.append({
            'team': team, 'season': season,
            # CFBD's percentPPA is the headline "% of production returning"
            'returning_offense_pct': _f(r.get('percentPPA')),
            'returning_defense_pct': None,  # not provided by CFBD
            'returning_pass_yards': _f(r.get('totalPassingPPA')),
            'returning_rush_yards': _f(r.get('totalRushingPPA')),
            'returning_receiving_yards': _f(r.get('totalReceivingPPA')),
            'returning_ppa': _f(r.get('totalPPA')),
            'updated_at': now,
        })
    if not rows: return 0
    top5 = sorted([r for r in rows if r['returning_offense_pct'] is not None],
                   key=lambda r: -r['returning_offense_pct'])[:5]
    print(f'  top-5 returning offense %:')
    for r in top5:
        print(f'    {r["team"]:22s} {r["returning_offense_pct"]:.3f}')
    bot5 = sorted([r for r in rows if r['returning_offense_pct'] is not None],
                   key=lambda r: r['returning_offense_pct'])[:5]
    print(f'  bottom-5 (biggest rebuilds):')
    for r in bot5:
        print(f'    {r["team"]:22s} {r["returning_offense_pct"]:.3f}')
    n = upsert(rows, dry_run=dry_run)
    print(f'\n  ✓ upserted {n}')
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int)
    ap.add_argument('--all-seasons', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.all_seasons:
        for s in (2022, 2023, 2024, 2025, 2026):
            run(s, dry_run=args.dry_run)
    elif args.season:
        run(args.season, dry_run=args.dry_run)
    else:
        print('specify --season or --all-seasons')


if __name__ == '__main__':
    main()
