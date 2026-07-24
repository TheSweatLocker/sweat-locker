"""NCAAF team stats puller (Phase 1 — item 1).

Populates ncaaf_team_stats from CFBD endpoints:
  /stats/season/advanced  — offensive/defensive EPA per play (CFBD calls it 'ppa'),
                            success rate, explosiveness
  /ratings/sp             — SP+ overall/offense/defense ratings

Runs weekly on Tuesday cron during CFB season (Aug-Jan). One-shot backfill
covers 2022-2025 for cohort baselines + current year model inputs.

USAGE:
    python ncaaf_stats_pull.py                       # current season (auto)
    python ncaaf_stats_pull.py --season 2024         # specific season
    python ncaaf_stats_pull.py --start 2022 --end 2025  # backfill range
"""
import argparse
import os
import sys
from datetime import datetime
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
CFBD_KEY = os.environ.get('CFBD_API_KEY')

H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

CFBD_BASE = 'https://api.collegefootballdata.com'


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(float(v)) if v is not None else None
    except (TypeError, ValueError): return None


def cfbd_get(path: str, params: dict) -> list:
    if not CFBD_KEY:
        print(f'  ✗ CFBD_API_KEY missing')
        return []
    r = requests.get(
        f'{CFBD_BASE}{path}',
        headers={'Authorization': f'Bearer {CFBD_KEY}'},
        params=params, timeout=30,
    )
    if r.status_code != 200:
        print(f'  ⚠ CFBD {path} {r.status_code}: {r.text[:120]}')
        return []
    return r.json()


def fetch_advanced_stats(season: int) -> dict:
    """Return {team: {off_epa, off_success, off_explosive, def_*, ...}}."""
    rows = cfbd_get('/stats/season/advanced', {'year': season})
    out = {}
    for row in rows:
        team = row.get('team')
        if not team: continue
        off = row.get('offense') or {}
        deff = row.get('defense') or {}
        # CFBD names ppa but stores what MLB pipeline calls EPA. Same signal.
        out[team] = {
            'off_epa_per_play':   _f(off.get('ppa')),
            'off_pass_epa':       _f((off.get('passingPlays') or {}).get('ppa')) if isinstance(off.get('passingPlays'), dict) else None,
            'off_rush_epa':       _f((off.get('rushingPlays') or {}).get('ppa')) if isinstance(off.get('rushingPlays'), dict) else None,
            'off_success_rate':   _f(off.get('successRate')),
            'off_explosiveness':  _f(off.get('explosiveness')),
            'def_epa_per_play':   _f(deff.get('ppa')),
            'def_pass_epa':       _f((deff.get('passingPlays') or {}).get('ppa')) if isinstance(deff.get('passingPlays'), dict) else None,
            'def_rush_epa':       _f((deff.get('rushingPlays') or {}).get('ppa')) if isinstance(deff.get('rushingPlays'), dict) else None,
            'def_success_rate':   _f(deff.get('successRate')),
        }
    return out


def fetch_sp_ratings(season: int) -> dict:
    """Return {team: {sp_overall, sp_offense, sp_defense}}."""
    rows = cfbd_get('/ratings/sp', {'year': season})
    out = {}
    for row in rows:
        team = row.get('team')
        if not team: continue
        off = row.get('offense') or {}
        deff = row.get('defense') or {}
        out[team] = {
            'sp_overall': _f(row.get('rating')),
            'sp_offense': _f(off.get('rating')),
            'sp_defense': _f(deff.get('rating')),
        }
    return out


def _normalize_batch_keys(rows: list) -> list:
    """PostgREST batch upsert union-normalize — see backfill script."""
    if not rows: return rows
    keys = set()
    for r in rows: keys.update(r.keys())
    for r in rows:
        for k in keys: r.setdefault(k, None)
    return rows


def upsert(rows: list) -> int:
    if not rows: return 0
    rows = _normalize_batch_keys(rows)
    r = requests.post(
        f'{SB}/rest/v1/ncaaf_team_stats?on_conflict=team,season,season_type',
        headers=H_WRITE, json=rows, timeout=60,
    )
    if r.status_code in (200, 201, 204):
        return len(rows)
    print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
    return 0


def run(seasons: list) -> None:
    print(f'=== NCAAF team stats pull (seasons {seasons}) ===')
    if not CFBD_KEY:
        print('  ✗ CFBD_API_KEY missing — abort')
        return
    total = 0
    for season in seasons:
        print(f'\n--- Season {season} ---')
        adv = fetch_advanced_stats(season)
        sp = fetch_sp_ratings(season)
        print(f'  advanced stats: {len(adv)} teams · SP+ ratings: {len(sp)} teams')
        team_set = set(adv.keys()) | set(sp.keys())
        rows = []
        for team in sorted(team_set):
            row = {
                'team': team,
                'season': season,
                'season_type': 'regular',
                'updated_at': datetime.utcnow().isoformat(),
            }
            row.update(adv.get(team, {}))
            row.update(sp.get(team, {}))
            rows.append(row)
        n = upsert(rows)
        print(f'  ✓ upserted {n} team-season rows')
        total += n
    print(f'\n=== Total: {total} team-season rows ===')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=None)
    ap.add_argument('--start', type=int, default=None)
    ap.add_argument('--end', type=int, default=None)
    args = ap.parse_args()
    if args.season:
        seasons = [args.season]
    elif args.start and args.end:
        seasons = list(range(args.start, args.end + 1))
    else:
        # Default: current year (or previous if pre-season month)
        seasons = [datetime.now().year if datetime.now().month >= 7 else datetime.now().year - 1]
    run(seasons)


if __name__ == '__main__':
    main()
