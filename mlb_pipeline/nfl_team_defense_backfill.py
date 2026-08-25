"""NFL team defense stats backfill (2026-08-09 · Phase 2).

Current gap: `nfl_team_stats` only has offense-side EPA. Per-game
projections are stuck at flat 44.5 because there's no opponent-defense
signal to differentiate matchups.

This script derives per-team defense stats FROM game results:
  - def_ppg          points allowed per game
  - def_ypg          total yards allowed per game (from offense yards)
  - def_pass_epa_allowed  average opponent pass_epa per game
  - def_rush_epa_allowed  average opponent rush_epa per game

Writes to a new `nfl_team_defense_stats` table (created here) so
we don't muddy nfl_team_stats (upstream nflverse pull).

CLI:
    python nfl_team_defense_backfill.py --season 2025
    python nfl_team_defense_backfill.py --all-seasons
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

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
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'resolution=merge-duplicates,return=minimal'}


def load_results(season: int) -> list:
    r = requests.get(f'{SB}/rest/v1/nfl_game_results', headers=H,
        params={'season': f'eq.{season}', 'select':'*', 'limit':'500'}, timeout=30)
    return r.json() if isinstance(r.json(), list) else []


def load_team_stats(season: int) -> dict:
    r = requests.get(f'{SB}/rest/v1/nfl_team_stats', headers=H,
        params={'season': f'eq.{season}', 'season_type': 'eq.reg',
                'select':'*'}, timeout=30)
    return {row['team']: row for row in (r.json() if isinstance(r.json(),list) else [])}


def backfill(season: int, dry_run: bool = False):
    print(f'=== NFL defense backfill · season {season} ===')
    results = load_results(season)
    if not results:
        print(f'  no results for season {season}'); return 0
    print(f'  {len(results)} game results')
    stats_map = load_team_stats(season)
    print(f'  {len(stats_map)} team offense stat rows')

    # Aggregate opponent stats per team
    # For each game: home team's DEFENSE faced away team's OFFENSE
    #   → def_pts_allowed += away_score
    #   → def_pass_epa_allowed += away.pass_epa / away.games (avg per game)
    def_agg = defaultdict(lambda: {'games':0, 'pts_allowed':0, 'yds_allowed':0,
                                    'pass_epa_allowed':0.0, 'rush_epa_allowed':0.0})
    for g in results:
        home, away = g.get('home_team'), g.get('away_team')
        hs, as_ = g.get('home_score'), g.get('away_score')
        if not home or not away or hs is None or as_ is None: continue
        # Home defense faced away offense
        away_off = stats_map.get(away, {})
        away_games = float(away_off.get('games') or 0) or 1
        away_pass_yds = float(away_off.get('pass_yards') or 0)
        away_rush_yds = float(away_off.get('rush_yards') or 0)
        away_yds_per_game = (away_pass_yds + away_rush_yds) / away_games
        away_pass_epa_pg = float(away_off.get('pass_epa') or 0) / away_games
        away_rush_epa_pg = float(away_off.get('rush_epa') or 0) / away_games
        def_agg[home]['games'] += 1
        def_agg[home]['pts_allowed'] += as_
        def_agg[home]['yds_allowed'] += away_yds_per_game  # season-avg proxy
        def_agg[home]['pass_epa_allowed'] += away_pass_epa_pg
        def_agg[home]['rush_epa_allowed'] += away_rush_epa_pg
        # Away defense faced home offense
        home_off = stats_map.get(home, {})
        home_games = float(home_off.get('games') or 0) or 1
        home_pass_yds = float(home_off.get('pass_yards') or 0)
        home_rush_yds = float(home_off.get('rush_yards') or 0)
        home_yds_per_game = (home_pass_yds + home_rush_yds) / home_games
        home_pass_epa_pg = float(home_off.get('pass_epa') or 0) / home_games
        home_rush_epa_pg = float(home_off.get('rush_epa') or 0) / home_games
        def_agg[away]['games'] += 1
        def_agg[away]['pts_allowed'] += hs
        def_agg[away]['yds_allowed'] += home_yds_per_game
        def_agg[away]['pass_epa_allowed'] += home_pass_epa_pg
        def_agg[away]['rush_epa_allowed'] += home_rush_epa_pg

    # Compute per-game averages + write
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for team, d in def_agg.items():
        n = d['games']
        if n < 1: continue
        rows.append({
            'team': team, 'season': season, 'season_type': 'reg',
            'games': n,
            'def_ppg': round(d['pts_allowed'] / n, 2),
            'def_ypg': round(d['yds_allowed'] / n, 2),
            'def_pass_epa_allowed': round(d['pass_epa_allowed'] / n, 4),
            'def_rush_epa_allowed': round(d['rush_epa_allowed'] / n, 4),
            'updated_at': now,
        })
    print(f'\n=== DEFENSE STATS (top+bottom by def_ppg) ===')
    rows_sorted = sorted(rows, key=lambda r: r['def_ppg'])
    print(f'  BEST DEFENSES (fewest ppg):')
    for r in rows_sorted[:5]:
        print(f'    {r["team"]:4s} PPG={r["def_ppg"]:5.1f} YPG={r["def_ypg"]:6.1f} '
              f'pEPA={r["def_pass_epa_allowed"]:+.3f}')
    print(f'  WORST DEFENSES (most ppg):')
    for r in rows_sorted[-5:]:
        print(f'    {r["team"]:4s} PPG={r["def_ppg"]:5.1f} YPG={r["def_ypg"]:6.1f} '
              f'pEPA={r["def_pass_epa_allowed"]:+.3f}')

    if dry_run:
        print(f'\n  [DRY] would upsert {len(rows)} rows to nfl_team_defense_stats')
        return len(rows)

    # Upsert. Table created via migration below.
    for i in range(0, len(rows), 50):
        chunk = rows[i:i+50]
        wr = requests.post(
            f'{SB}/rest/v1/nfl_team_defense_stats?on_conflict=team,season,season_type',
            headers=H_W, data=json.dumps(chunk, default=str), timeout=15)
        if wr.status_code not in (200, 201, 204):
            print(f'  ⚠ chunk {i} write failed {wr.status_code}: {wr.text[:200]}')
    print(f'\n  ✓ upserted {len(rows)} def stat rows for {season}')
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int)
    ap.add_argument('--all-seasons', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.all_seasons:
        for s in (2022, 2023, 2024, 2025):
            backfill(s, dry_run=args.dry_run)
    elif args.season:
        backfill(args.season, dry_run=args.dry_run)
    else:
        print('specify --season or --all-seasons')


if __name__ == '__main__':
    main()
