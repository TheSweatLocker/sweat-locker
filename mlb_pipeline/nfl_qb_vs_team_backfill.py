"""Backfill nfl_qb_vs_team from nflverse play-by-play data.

Aggregates QB stats per opponent defense: career (all-time) + recent (last 3 vs
this opp). Runs weekly after Sunday games grade.

Data source: nflverse weekly QB stats (public GitHub CSV).
  https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats.csv.gz

Design mirrors MLB mlb_game_context vs-team pattern — enables signals like
qb_vs_team_career_yds_high, qb_vs_team_recent_dominant, etc.

Usage:
    python nfl_qb_vs_team_backfill.py                   # full rebuild (last 5 seasons)
    python nfl_qb_vs_team_backfill.py --seasons 2024,2025  # specific seasons
    python nfl_qb_vs_team_backfill.py --qb "Josh Allen"  # single QB test
    python nfl_qb_vs_team_backfill.py --dry-run

Migration: 20260821_nfl_qb_vs_team.sql must be applied first.
"""
import argparse
import io
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
for line in _env.read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json',
      'Prefer': 'resolution=merge-duplicates,return=minimal'}

# nflverse player stats endpoint (weekly, all seasons back to 1999)
NFLVERSE_STATS_URL = (
    'https://github.com/nflverse/nflverse-data/releases/download/'
    'player_stats/player_stats.csv'
)


def fetch_qb_gamelogs(seasons: list[int], qb_filter: str = None) -> list[dict]:
    """Fetch QB game-by-game stats from nflverse. Filter to QBs only, target
    seasons, and optionally a single QB name."""
    import csv
    print(f'  fetching nflverse player_stats (seasons: {seasons}) ...')
    try:
        r = requests.get(NFLVERSE_STATS_URL, timeout=120, allow_redirects=True)
    except Exception as e:
        print(f'  ✗ fetch failed: {e}')
        return []
    if r.status_code != 200:
        print(f'  ✗ HTTP {r.status_code}')
        return []
    text = r.content.decode('utf-8', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        try:
            season = int(row.get('season') or 0)
            pos = row.get('position') or ''
            attempts = int(float(row.get('attempts') or 0))
        except (ValueError, TypeError):
            continue
        if season not in seasons: continue
        if pos != 'QB': continue
        if attempts < 10: continue  # skip token appearances
        if qb_filter and qb_filter.lower() not in (row.get('player_display_name', '') or '').lower():
            continue
        rows.append(row)
    print(f'  loaded {len(rows)} QB game rows')
    return rows


def aggregate_by_qb_opp(rows: list[dict]) -> dict:
    """Aggregate rows into per-(qb, opponent) buckets: career + recent 3."""
    # Group by (player_id, opponent_team) then sort by week for recent slicing
    buckets = defaultdict(list)
    for r in rows:
        qb_id = r.get('player_id')
        opp = r.get('opponent_team')
        if not qb_id or not opp: continue
        buckets[(qb_id, opp)].append(r)

    aggregated = {}
    for (qb_id, opp), games in buckets.items():
        # Sort chronologically (season + week)
        games.sort(key=lambda g: (int(g.get('season') or 0), int(g.get('week') or 0)))
        qb_name = games[0].get('player_display_name', '')

        def sum_int(games, field):
            total = 0
            for g in games:
                try: total += int(float(g.get(field) or 0))
                except (ValueError, TypeError): pass
            return total
        def sum_float(games, field):
            total = 0.0
            for g in games:
                try: total += float(g.get(field) or 0)
                except (ValueError, TypeError): pass
            return total

        # Career
        career_starts = len(games)
        career_yds = sum_int(games, 'passing_yards')
        career_td = sum_int(games, 'passing_tds')
        career_int = sum_int(games, 'interceptions')
        career_cmp = sum_int(games, 'completions')
        career_att = sum_int(games, 'attempts')
        career_sacks = sum_int(games, 'sacks_suffered')
        career_rush_yds = sum_int(games, 'rushing_yards')
        career_rush_td = sum_int(games, 'rushing_tds')
        # QB rating standard formula
        cmp_pct = (career_cmp / career_att) if career_att else 0
        yds_per_att = (career_yds / career_att) if career_att else 0
        td_int = (career_td / max(career_int, 1))
        # Passer rating (NFL formula, capped)
        def nfl_rating(cmp, att, yds, td, ints):
            if att < 1: return 0
            a = max(0, min(2.375, ((cmp/att) - 0.3) * 5))
            b = max(0, min(2.375, ((yds/att) - 3) * 0.25))
            c = max(0, min(2.375, (td/att) * 20))
            d = max(0, min(2.375, 2.375 - (ints/att) * 25))
            return round((a + b + c + d) / 6 * 100, 1)
        career_qb_rating = nfl_rating(career_cmp, career_att, career_yds, career_td, career_int)

        # Recent (last 3)
        recent = games[-3:]
        recent_n = len(recent)
        r_yds = sum_int(recent, 'passing_yards') / max(recent_n, 1)
        r_td = sum_int(recent, 'passing_tds') / max(recent_n, 1)
        r_int = sum_int(recent, 'interceptions') / max(recent_n, 1)
        r_cmp = sum_int(recent, 'completions')
        r_att = sum_int(recent, 'attempts')
        r_cmp_pct = (r_cmp / r_att) if r_att else 0
        r_yds_tot = sum_int(recent, 'passing_yards')
        r_td_tot = sum_int(recent, 'passing_tds')
        r_int_tot = sum_int(recent, 'interceptions')
        r_qb_rating = nfl_rating(r_cmp, r_att, r_yds_tot, r_td_tot, r_int_tot)

        last_date = games[-1].get('season') + '-week' + games[-1].get('week', '?')

        aggregated[(qb_id, opp)] = {
            'qb_id': qb_id, 'qb_name': qb_name, 'opponent_team': opp,
            'career_starts': career_starts,
            'career_pass_yds': career_yds, 'career_pass_td': career_td,
            'career_int': career_int, 'career_completions': career_cmp,
            'career_attempts': career_att, 'career_sacks_taken': career_sacks,
            'career_rush_yds': career_rush_yds, 'career_rush_td': career_rush_td,
            'career_cmp_pct': round(cmp_pct, 3),
            'career_yds_per_att': round(yds_per_att, 2),
            'career_td_int_ratio': round(td_int, 2),
            'career_qb_rating': career_qb_rating,
            'recent_n_starts': recent_n,
            'recent_pass_yds_avg': round(r_yds, 1),
            'recent_pass_td_avg': round(r_td, 2),
            'recent_int_avg': round(r_int, 2),
            'recent_cmp_pct': round(r_cmp_pct, 3),
            'recent_qb_rating': r_qb_rating,
            'last_updated_at': datetime.now(timezone.utc).isoformat(),
        }
    return aggregated


def upsert_bulk(rows: list[dict], dry_run: bool = False) -> int:
    if dry_run:
        print(f'  [DRY] would upsert {len(rows)} rows')
        return 0
    if not rows: return 0
    # PostgREST batches — chunk to 500
    n = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i+500]
        r = requests.post(
            f'{SB}/rest/v1/nfl_qb_vs_team?on_conflict=qb_id,opponent_team',
            headers=HW, json=chunk, timeout=60)
        if r.status_code in (200, 201, 204):
            n += len(chunk)
        else:
            print(f'  ✗ upsert failed {r.status_code}: {r.text[:200]}')
    return n


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seasons', default='2021,2022,2023,2024,2025',
                   help='Comma-separated season years')
    p.add_argument('--qb', help='Filter to single QB name (substring)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    seasons = [int(s.strip()) for s in args.seasons.split(',')]
    print(f'=== nfl_qb_vs_team_backfill · seasons {seasons} ===')

    rows = fetch_qb_gamelogs(seasons, args.qb)
    if not rows:
        print('no rows fetched')
        return

    agg = aggregate_by_qb_opp(rows)
    print(f'  aggregated: {len(agg)} (qb, opp) buckets')

    upserted = upsert_bulk(list(agg.values()), args.dry_run)
    print(f'\n✓ upserted {upserted} nfl_qb_vs_team rows')


if __name__ == '__main__':
    main()
