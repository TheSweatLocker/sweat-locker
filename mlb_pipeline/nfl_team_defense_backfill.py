"""NFL team defense stats backfill (2026-08-09, reopened 2026-08-28).

Fills the gap where nfl_team_stats only holds offense-side metrics and
defensive event counts (sacks, INTs, fumbles forced) — but no yards-
allowed pass/rush or points-allowed-per-game. Bettors + Jerry need
these to reason about matchup defense.

Method: for each finalized game in nfl_game_results, opponent's SEASONAL
offense (from nfl_team_stats) is attributed to this team's defense. Sum
+ average across the season → per-game defensive coverage stats.

Writes to `nfl_team_defense_stats` (migration 20260828c). Upsert on
(team, season, season_type). Refreshes idempotently.

2026-08-28 additions:
  * def_pass_ypg and def_rush_ypg — the earlier version only had
    combined def_ypg. User now wants pass vs rush yards allowed
    separately for the game-detail team stats section.
  * def_ppg unchanged (opponent points per game, direct from scores).
  * Uses paginated fetch (>1000 games from multi-season history).

CLI:
    python nfl_team_defense_backfill.py --season 2025
    python nfl_team_defense_backfill.py --all-seasons
    python nfl_team_defense_backfill.py --dry-run
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
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}


def load_results(season: int) -> list:
    """Paginated pull of nfl_game_results for one season."""
    out = []
    for off in range(0, 10000, 1000):
        r = requests.get(f'{SB}/rest/v1/nfl_game_results', headers=H,
            params={'season': f'eq.{season}', 'select': '*',
                    'limit': 1000, 'offset': off}, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list): break
        out.extend(chunk)
        if len(chunk) < 1000: break
    return out


def load_team_stats(season: int) -> dict:
    r = requests.get(f'{SB}/rest/v1/nfl_team_stats', headers=H,
        params={'season': f'eq.{season}', 'season_type': 'eq.REG',
                'select': '*'}, timeout=30)
    return {row['team']: row for row in (r.json() if isinstance(r.json(), list) else [])}


def backfill(season: int, dry_run: bool = False) -> int:
    print(f'=== NFL defense backfill · season {season} ===')
    results = load_results(season)
    if not results:
        print(f'  no results for season {season}'); return 0
    print(f'  {len(results)} game results')
    stats_map = load_team_stats(season)
    print(f'  {len(stats_map)} team offense stat rows')

    # Aggregate opponent stats per team.
    # For each game: home team's DEFENSE faced away team's OFFENSE.
    # We add opponent's per-game averages to this team's defense tally,
    # then average again across games in write phase.
    def_agg = defaultdict(lambda: {
        'games': 0, 'pts_allowed': 0,
        'pass_yds_allowed': 0.0, 'rush_yds_allowed': 0.0,
        'pass_epa_allowed': 0.0, 'rush_epa_allowed': 0.0,
    })

    def _attribute(defender: str, offender_stats: dict, pts_scored: int) -> None:
        n = float(offender_stats.get('games') or 0) or 1
        pass_yds = float(offender_stats.get('pass_yards') or 0) / n
        rush_yds = float(offender_stats.get('rush_yards') or 0) / n
        pass_epa = float(offender_stats.get('pass_epa') or 0) / n
        rush_epa = float(offender_stats.get('rush_epa') or 0) / n
        def_agg[defender]['games'] += 1
        def_agg[defender]['pts_allowed'] += pts_scored
        def_agg[defender]['pass_yds_allowed'] += pass_yds
        def_agg[defender]['rush_yds_allowed'] += rush_yds
        def_agg[defender]['pass_epa_allowed'] += pass_epa
        def_agg[defender]['rush_epa_allowed'] += rush_epa

    for g in results:
        home, away = g.get('home_team'), g.get('away_team')
        hs, as_ = g.get('home_score'), g.get('away_score')
        if not home or not away or hs is None or as_ is None: continue
        _attribute(home, stats_map.get(away, {}), as_)
        _attribute(away, stats_map.get(home, {}), hs)

    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for team, d in def_agg.items():
        n = d['games']
        if n < 1: continue
        rows.append({
            'team': team, 'season': season, 'season_type': 'REG',
            'games': n,
            'def_ppg': round(d['pts_allowed'] / n, 2),
            'def_pass_ypg': round(d['pass_yds_allowed'] / n, 2),
            'def_rush_ypg': round(d['rush_yds_allowed'] / n, 2),
            'def_ypg': round((d['pass_yds_allowed'] + d['rush_yds_allowed']) / n, 2),
            'def_pass_epa_allowed': round(d['pass_epa_allowed'] / n, 4),
            'def_rush_epa_allowed': round(d['rush_epa_allowed'] / n, 4),
            'updated_at': now,
        })

    if rows:
        rows_sorted = sorted(rows, key=lambda r: r['def_ppg'])
        print(f'\n  BEST DEFENSES (fewest ppg):')
        for r in rows_sorted[:5]:
            print(f'    {r["team"]:4s} PPG={r["def_ppg"]:5.1f} pass_YPG={r["def_pass_ypg"]:6.1f} '
                  f'rush_YPG={r["def_rush_ypg"]:6.1f} pEPA={r["def_pass_epa_allowed"]:+.3f}')
        print(f'  WORST DEFENSES:')
        for r in rows_sorted[-5:]:
            print(f'    {r["team"]:4s} PPG={r["def_ppg"]:5.1f} pass_YPG={r["def_pass_ypg"]:6.1f} '
                  f'rush_YPG={r["def_rush_ypg"]:6.1f} pEPA={r["def_pass_epa_allowed"]:+.3f}')

    if dry_run:
        print(f'\n  [DRY] would upsert {len(rows)} rows')
        return len(rows)

    written = 0
    for i in range(0, len(rows), 50):
        chunk = rows[i:i+50]
        wr = requests.post(
            f'{SB}/rest/v1/nfl_team_defense_stats?on_conflict=team,season,season_type',
            headers=H_W, data=json.dumps(chunk, default=str), timeout=15)
        if wr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ chunk {i} write failed {wr.status_code}: {wr.text[:200]}')
    print(f'\n  ✓ upserted {written}/{len(rows)} def stat rows for {season}')
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int)
    ap.add_argument('--all-seasons', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.all_seasons:
        for s in (2022, 2023, 2024, 2025, 2026):
            backfill(s, dry_run=args.dry_run)
    elif args.season:
        backfill(args.season, dry_run=args.dry_run)
    else:
        # Default: current season if no arg
        current = (datetime.now(timezone.utc)).year
        print(f'no --season / --all-seasons — defaulting to {current}')
        backfill(current, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
