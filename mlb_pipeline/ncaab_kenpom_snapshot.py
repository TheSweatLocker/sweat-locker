"""KenPom rating-snapshot writer (2026-08-14).

NCAAB Session 1 · rating source #1.

The existing ncaab_pipeline.py pulls KenPom + writes to ncaab_team_stats
(KenPom's full 33-column schema). This script pulls the SAME API but
writes to ncaab_rating_snapshots — the sport-agnostic multi-system
view the Panel model reads.

Runs alongside ncaab_pipeline (not a replacement). Same API call, two
destinations. Duplication acceptable because the two consumers want
different shapes:
  * ncaab_team_stats  → KenPom-specific (all 33 fields, four factors)
  * ncaab_rating_snapshots → cross-system uniform (adj_off/def/em/tempo)

Sport-universal Panel model queries ncaab_rating_snapshots by
snapshot_date + season, aggregates across (kenpom, torvik, massey,
haslam) — same shape data from every source.

CLI:
    python ncaab_kenpom_snapshot.py [--season 2026] [--dry-run]

season is the calendar year at END of season (KenPom convention).
2025-26 season → season=2026.
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
KENPOM_KEY = os.environ.get('KENPOM_KEY') or os.environ.get('EXPO_PUBLIC_KENPOM_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


def fetch_kenpom_ratings(season_year: int) -> list:
    """Pull the ratings endpoint. Returns list of team dicts."""
    if not KENPOM_KEY:
        print('  ✗ KENPOM_KEY missing')
        return []
    r = requests.get('https://kenpom.com/api.php',
        params={'endpoint': 'ratings', 'y': str(season_year)},
        headers={'Authorization': f'Bearer {KENPOM_KEY}'},
        timeout=30)
    if r.status_code != 200:
        print(f'  ✗ KenPom HTTP {r.status_code}: {r.text[:200]}')
        return []
    data = r.json()
    return data if isinstance(data, list) else []


def _season_str(season_year: int) -> str:
    """Convert '2026' → '2025-26' to match ncaab_team_stats convention."""
    return f'{season_year - 1}-{str(season_year)[-2:]}'


def upsert_snapshot(teams: list, season_year: int, snapshot_date: date,
                    dry_run: bool = False) -> int:
    from data_quality import DQ
    dq = DQ(source='ncaab_kenpom_snapshot.py', sport='NCAAB')

    dq.assert_range(len(teams), 300, 400, 'kenpom_team_count',
                     context={'season': season_year, 'n_teams': len(teams)})
    if len(teams) < 100:
        print(f'  ✗ suspiciously few teams ({len(teams)})')
        return 0

    season_str = _season_str(season_year)
    now = datetime.now(timezone.utc).isoformat()
    payloads = []
    for t in teams:
        team_name = t.get('TeamName')
        if not team_name: continue
        adj_em = t.get('AdjEM')
        adj_oe = t.get('AdjOE')
        adj_de = t.get('AdjDE')
        if adj_em is None or adj_oe is None or adj_de is None: continue

        # Range guards
        dq.assert_range(adj_em, -50, 50, 'kenpom_adj_em',
                         context={'team': team_name, 'value': adj_em})
        dq.assert_range(adj_oe, 60, 150, 'kenpom_adj_oe',
                         context={'team': team_name, 'value': adj_oe})

        payloads.append({
            'snapshot_date': snapshot_date.isoformat(),
            'team': team_name,
            'season': season_str,
            'rating_system': 'kenpom',
            'adj_off': adj_oe,
            'adj_def': adj_de,
            'adj_em': adj_em,
            'tempo': t.get('AdjTempo') or t.get('Tempo'),
            'em_rank': t.get('RankAdjEM'),
            'raw_payload': {
                'conference': t.get('ConfShort'),
                'coach': t.get('Coach'),
                'wins': t.get('Wins'),
                'losses': t.get('Losses'),
                'seed': t.get('Seed'),
                'pythag': t.get('Pythag'),
                'luck': t.get('Luck'),
                'data_through': t.get('DataThrough'),
            },
            'computed_at': now,
        })

    if dry_run:
        print(f'  [DRY] would upsert {len(payloads)} kenpom snapshots')
        for p in payloads[:3]:
            print(f'    {p["team"]:22} em={p["adj_em"]} off={p["adj_off"]} def={p["adj_def"]} rank={p["em_rank"]}')
        return len(payloads)

    written = 0
    for i in range(0, len(payloads), 200):
        chunk = payloads[i:i+200]
        pr = requests.post(
            f'{SB}/rest/v1/ncaab_rating_snapshots?on_conflict=snapshot_date,team,season,rating_system',
            headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:200]}')
    print(f'  ✓ wrote {written} kenpom snapshots')
    return written


def run(season_year: int, dry_run: bool = False) -> int:
    print(f'=== ncaab_kenpom_snapshot · season {season_year} ({_season_str(season_year)}) ===')
    teams = fetch_kenpom_ratings(season_year)
    print(f'  fetched {len(teams)} teams from KenPom API')
    if not teams: return 0
    today = datetime.now(timezone.utc).date()
    return upsert_snapshot(teams, season_year, today, dry_run=dry_run)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', type=int, default=None,
        help='calendar year at end of season (e.g., 2026 for 2025-26). Defaults to current NCAAB season.')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    # Default season: if now is Nov-June, we're in season Y-1 → Y (return Y).
    # July-Oct offseason → last completed season.
    if args.season is None:
        now = datetime.now(timezone.utc)
        args.season = now.year if now.month >= 11 else (now.year if now.month <= 6 else now.year)
    run(args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
