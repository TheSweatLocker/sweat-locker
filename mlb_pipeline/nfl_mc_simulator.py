"""NFL Monte Carlo simulator (2026-08-13).

The 5th lens in NFL's model stack. Runs 10,000-game Monte Carlo per matchup
using EPA-derived expected points + HFA + variance, writes mc_probabilities
blob to nfl_game_context (same shape as MLB's).

v1 architecture (MVP — ships by 2026-09-04 for Week 1):
  * Team offensive strength = (pass_epa + rush_epa) / games
    (net expected-points-added generated per game, offense side)
  * Team defensive strength = league-average adjusted by pass/rush allowed
    (proxied from opponent aggregate; refined in v2 with team-vs-team splits)
  * Expected points per side = LEAGUE_AVG_PPG + (offense_epa - opp_defense_epa)
  * HFA = +2.5 points to home team (historical NFL average)
  * Sample: 10,000 games with normal distribution around expected points,
    stddev = 10.5 (calibrated to NFL historical variance)
  * Outputs: mc_p_home, mc_p_away, mc_expected_margin, mc_expected_total,
    mc_stddev_margin, mc_p_over_line, mc_confidence_high

v2 roadmap (Week 4+):
  * Drive-by-drive sim (22-26 possessions per game)
  * Red-zone efficiency conditional on drive entry
  * Weather adjustment (wind speed reduces pass EPA)
  * Injury deltas (QB out = -8 points offense, star DE out = +3 opp offense)
  * Rest / travel / short-week adjustments

Sport-scoped — reads nfl_game_context + nfl_team_stats; writes back to
nfl_game_context.mc_probabilities. Skips preseason games (stats_source =
'preseason') per the same discipline as primary_play.

CLI:
    python nfl_mc_simulator.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, json, math, random
from datetime import datetime, timedelta, timezone
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
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# NFL historical averages — v1 constants.
LEAGUE_AVG_PPG = 22.5     # 2020-2024 NFL points per game per team
HFA_POINTS = 2.5          # home field advantage in points
GAME_STDDEV = 10.5        # per-team score standard deviation (historical)
N_SIMS = 10000            # sim count per game
MIN_GAMES_FOR_STATS = 4   # minimum games in season before using team's stats
                          # (below this, fall back to league average +
                          # small regression). Handles Week 1-4 uncertainty.


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def load_team_stats(season: int) -> dict:
    """Load current season NFL team stats keyed by team abbreviation."""
    r = requests.get(f'{SB}/rest/v1/nfl_team_stats', headers=H_READ,
        params={'season': f'eq.{season}', 'season_type': 'eq.REG',
                'select': 'team,games,pass_epa,rush_epa,pass_attempts,rush_attempts,'
                          'sacks_suffered,pass_ints'},
        timeout=15)
    if r.status_code != 200: return {}
    return {row['team']: row for row in r.json()}


def team_offensive_strength(team_row: dict) -> Optional[float]:
    """Points-added-per-game the team's offense produces vs league average.
    Positive = above average offense. Falls back to 0 (league avg) when
    fewer than MIN_GAMES_FOR_STATS games have been played."""
    games = team_row.get('games') or 0
    if games < MIN_GAMES_FOR_STATS: return 0.0
    pass_epa = float(team_row.get('pass_epa') or 0)
    rush_epa = float(team_row.get('rush_epa') or 0)
    # Total EPA / games = net points-added per game vs neutral opponent
    # EPA is already in point-equivalent units so no conversion needed
    return (pass_epa + rush_epa) / games


def team_defensive_strength(team_row: dict, all_teams: dict) -> Optional[float]:
    """v1 proxy: opponents' scoring against this defense minus league avg.
    We don't have raw def_epa allowed in the schema — approximate by inverting
    offense_epa across the schedule. Falls back to sacks/ints proxy when
    schedule data thin.

    Returns POSITIVE for defenses that ADD points to opponents (bad defense).
    Negative = good defense (allows fewer points than average).

    v2 will use per-play def_epa_allowed once we backfill that column.
    """
    games = team_row.get('games') or 0
    if games < MIN_GAMES_FOR_STATS: return 0.0
    # Interim proxy: sacks + ints per game vs league average as inverse
    # defensive quality. A team with 3 sacks/game + 1 int/game = ~4 defensive
    # plays that swing ~2 pts each = -8 pts allowed vs average.
    sacks = float(team_row.get('def_sacks') or 0) if 'def_sacks' in team_row else 0
    ints = float(team_row.get('def_ints') or 0) if 'def_ints' in team_row else 0
    if games == 0: return 0.0
    disruption_per_game = (sacks + ints) / games
    # League avg: ~2.5 disruption plays/game. Delta × 2 pts each.
    return -1 * (disruption_per_game - 2.5) * 2.0


def simulate_game(home_off: float, away_off: float,
                  home_def: float, away_def: float,
                  n_sims: int = N_SIMS,
                  posted_total: Optional[float] = None) -> dict:
    """Run n_sims of the game with normal distribution around expected points.
    Home gets HFA_POINTS bonus. Returns mc_probabilities dict."""
    # Expected points per team.
    # home_expected = LEAGUE_AVG + home_offense - away_defense_delta + HFA
    home_expected = LEAGUE_AVG_PPG + home_off + away_def + HFA_POINTS
    away_expected = LEAGUE_AVG_PPG + away_off + home_def

    # Reasonable floors (an NFL team almost never scores below 6 pts)
    home_expected = max(home_expected, 10.0)
    away_expected = max(away_expected, 10.0)

    home_wins = 0
    over_hits = 0
    total_margins = 0.0
    total_totals = 0.0
    margin_sq_sum = 0.0

    for _ in range(n_sims):
        home_score = random.gauss(home_expected, GAME_STDDEV)
        away_score = random.gauss(away_expected, GAME_STDDEV)
        # NFL scores don't go negative
        home_score = max(home_score, 0)
        away_score = max(away_score, 0)
        margin = home_score - away_score
        total = home_score + away_score
        total_margins += margin
        total_totals += total
        margin_sq_sum += margin * margin
        if home_score > away_score: home_wins += 1
        if posted_total is not None and total > posted_total: over_hits += 1

    mean_margin = total_margins / n_sims
    mean_total = total_totals / n_sims
    var_margin = (margin_sq_sum / n_sims) - (mean_margin ** 2)
    std_margin = math.sqrt(max(var_margin, 0))

    p_home = home_wins / n_sims
    result = {
        'mc_p_home': round(p_home, 3),
        'mc_p_away': round(1 - p_home, 3),
        'mc_expected_margin': round(mean_margin, 2),
        'mc_expected_total': round(mean_total, 2),
        'mc_stddev_margin': round(std_margin, 2),
        # HIGH-CONF flag: > 6-pt expected margin AND stddev under 10.
        # Matches MLB's mc_confidence_high semantics (rare, high-conviction).
        'mc_confidence_high': (abs(mean_margin) > 6.0 and std_margin < 10.0),
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }
    if posted_total is not None:
        result['mc_p_over_line'] = round(over_hits / n_sims, 3)
    return result


def run(game_date: str, dry_run: bool = False) -> int:
    """Load today's NFL games + team stats; simulate each; upsert results."""
    print(f'=== NFL MC simulator · {game_date} ===')
    season = int(game_date[:4])
    team_stats = load_team_stats(season)
    print(f'  loaded {len(team_stats)} team stat rows')

    r = requests.get(f'{SB}/rest/v1/nfl_game_context', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'game_id,home_team,away_team,close_total,stats_source'},
        timeout=15)
    if r.status_code != 200:
        print(f'  fetch failed: {r.status_code}'); return 0
    games = r.json()
    if not isinstance(games, list) or not games:
        print(f'  no NFL games for {game_date}'); return 0
    print(f'  {len(games)} NFL games in context')

    written = 0
    skipped_preseason = 0
    skipped_no_stats = 0

    for g in games:
        # Preseason skip — matches primary_play discipline
        if g.get('stats_source') == 'preseason':
            skipped_preseason += 1
            continue

        home = g.get('home_team'); away = g.get('away_team')
        if not (home and away): continue
        home_row = team_stats.get(home)
        away_row = team_stats.get(away)
        # v1 early-season: fall back to league avg (0, 0) if team stats missing
        home_off = team_offensive_strength(home_row) if home_row else 0.0
        away_off = team_offensive_strength(away_row) if away_row else 0.0
        home_def = team_defensive_strength(home_row, team_stats) if home_row else 0.0
        away_def = team_defensive_strength(away_row, team_stats) if away_row else 0.0

        # Log stat availability
        if not home_row or not away_row: skipped_no_stats += 1

        result = simulate_game(
            home_off=home_off, away_off=away_off,
            home_def=home_def, away_def=away_def,
            posted_total=g.get('close_total'),
        )

        matchup = f"{away} @ {home}"
        marker = ' [thin data — Week 1 fallback]' if not home_row or not away_row else ''
        print(f'  {matchup:30}  home {result["mc_p_home"]*100:5.1f}%  '
              f'margin {result["mc_expected_margin"]:+5.1f}  '
              f'tot {result["mc_expected_total"]:5.1f}  '
              f'{"HIGH-CONF" if result["mc_confidence_high"] else ""}{marker}')

        if dry_run: continue

        pr = requests.patch(f'{SB}/rest/v1/nfl_game_context?game_id=eq.{g["game_id"]}',
            headers=H_WRITE, json={'mc_probabilities': result}, timeout=10)
        if pr.status_code in (200, 201, 204):
            written += 1
        else:
            print(f'    write failed: {pr.status_code} {pr.text[:150]}')

    print(f'\n{"[DRY] would write" if dry_run else "wrote"} {written} MC blobs · '
          f'skipped {skipped_preseason} preseason, {skipped_no_stats} thin-stats')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date or _et_today(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
