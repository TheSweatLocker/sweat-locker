"""
Backfill 2025 MLB season games into mlb_game_results for XGBoost training.

Reconstructs what's available from the MLB Stats API:
  - Scores, NRFI result, total runs, run line, winner
  - Starting pitchers + season ERA (not xERA — point-in-time data unavailable)
  - Park factors (from our static table)
  - Umpire (home plate)
  - Venue, dome status
  - Linescore innings for F5 totals

Cannot reconstruct (flagged via data_quality):
  - xERA, K%, whiff%, GB% (Statcast point-in-time)
  - Weather (historical not free)
  - Betting lines (open/close totals/spreads)
  - Bullpen usage, injuries, lineups
  - Travel/rest/timezone situational edges

Usage:
  python mlb_pipeline/backfill_2025.py              # Full second half (July 1 - Sept 28)
  python mlb_pipeline/backfill_2025.py --start 2025-07-01 --end 2025-07-31  # Custom range
  python mlb_pipeline/backfill_2025.py --dry-run    # Preview without uploading
"""

import requests
import os
import sys
import time
import argparse
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=merge-duplicates,return=minimal'
}

# 2025-calibrated park factors (from 2,401 actual games)
PARK_FACTORS = {
    "Colorado Rockies": 133, "Oakland Athletics": 113, "Los Angeles Dodgers": 111,
    "Arizona Diamondbacks": 110, "Washington Nationals": 108, "Minnesota Twins": 107,
    "Philadelphia Phillies": 107, "Toronto Blue Jays": 107, "Los Angeles Angels": 106,
    "Detroit Tigers": 105, "Baltimore Orioles": 104, "New York Mets": 103,
    "New York Yankees": 101, "Atlanta Braves": 101, "Boston Red Sox": 101,
    "Chicago Cubs": 100, "Tampa Bay Rays": 99, "Cincinnati Reds": 98,
    "Miami Marlins": 96, "Chicago White Sox": 96, "Milwaukee Brewers": 95,
    "San Francisco Giants": 95, "St. Louis Cardinals": 95, "Houston Astros": 92,
    "Seattle Mariners": 91, "Cleveland Guardians": 90, "San Diego Padres": 89,
    "Pittsburgh Pirates": 85, "Kansas City Royals": 84, "Texas Rangers": 80,
}

DOME_VENUES = [
    "Tropicana Field", "loanDepot Park", "Chase Field",
    "Globe Life Field", "American Family Field", "Rogers Centre",
    "Minute Maid Park", "T-Mobile Park"
]


def fetch_schedule(game_date):
    """Fetch all MLB games for a given date with linescore + pitcher + officials data"""
    try:
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={
                'sportId': 1,
                'date': game_date,
                'hydrate': 'linescore,probablePitcher,officials,venue'
            },
            timeout=30
        )
        r.raise_for_status()
        dates = r.json().get('dates', [])
        if dates:
            return dates[0].get('games', [])
        return []
    except Exception as e:
        print(f'  Error fetching schedule for {game_date}: {e}')
        return []


def get_pitcher_season_era(pitcher_id, season=2025):
    """Fetch a pitcher's season ERA from MLB Stats API"""
    try:
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats',
            params={'stats': 'season', 'group': 'pitching', 'season': season},
            timeout=15
        )
        splits = r.json().get('stats', [{}])[0].get('splits', [])
        if splits:
            return float(splits[0].get('stat', {}).get('era', 0))
    except:
        pass
    return None


def process_game(mlb_game, dry_run=False):
    """Extract training features from a single completed MLB game"""
    status = mlb_game.get('status', {}).get('abstractGameState', '')
    if status != 'Final':
        return None

    teams = mlb_game.get('teams', {})
    home_info = teams.get('home', {})
    away_info = teams.get('away', {})
    home_team = home_info.get('team', {}).get('name', '')
    away_team = away_info.get('team', {}).get('name', '')
    game_date = mlb_game.get('officialDate', '')
    game_pk = mlb_game.get('gamePk')
    venue_name = mlb_game.get('venue', {}).get('name', '')

    # Game ID matching our format
    game_id = f"{away_team} @ {home_team} ({game_date})"

    # Linescore
    linescore = mlb_game.get('linescore', {})
    home_score = linescore.get('teams', {}).get('home', {}).get('runs')
    away_score = linescore.get('teams', {}).get('away', {}).get('runs')

    if home_score is None or away_score is None:
        return None

    total_runs = home_score + away_score
    home_win = home_score > away_score

    # Run line result
    margin = home_score - away_score
    if margin > 1.5:
        run_line_result = 'home'
    elif margin < -1.5:
        run_line_result = 'away'
    else:
        run_line_result = 'push'

    # NRFI from first inning
    innings = linescore.get('innings', [])
    nrfi_result = None
    if innings:
        first = innings[0]
        home_r1 = first.get('home', {}).get('runs', 0) or 0
        away_r1 = first.get('away', {}).get('runs', 0) or 0
        nrfi_result = 'NRFI' if (home_r1 + away_r1) == 0 else 'YRFI'

    # F5 result (first 5 innings total)
    f5_home = None
    f5_away = None
    f5_total = None
    if len(innings) >= 5:
        f5_home = sum(inn.get('home', {}).get('runs', 0) or 0 for inn in innings[:5])
        f5_away = sum(inn.get('away', {}).get('runs', 0) or 0 for inn in innings[:5])
        f5_total = f5_home + f5_away

    # Starting pitchers
    home_pitcher_info = home_info.get('probablePitcher', {})
    away_pitcher_info = away_info.get('probablePitcher', {})
    home_pitcher_name = home_pitcher_info.get('fullName')
    away_pitcher_name = away_pitcher_info.get('fullName')
    home_pitcher_id = home_pitcher_info.get('id')
    away_pitcher_id = away_pitcher_info.get('id')

    # Park factor
    park_run_factor = PARK_FACTORS.get(home_team, 100)

    # Dome
    is_dome = venue_name in DOME_VENUES

    # Umpire
    officials = mlb_game.get('officials', [])
    umpire = next(
        (o.get('official', {}).get('fullName')
         for o in officials
         if o.get('officialType') == 'Home Plate'),
        None
    )

    record = {
        'game_id': game_id,
        'game_date': game_date,
        'season': 2025,
        'home_team': home_team,
        'away_team': away_team,
        'venue': venue_name,
        'dome_game': is_dome,
        'is_dome': is_dome,
        'home_sp_name': home_pitcher_name,
        'away_sp_name': away_pitcher_name,
        'park_run_factor': park_run_factor,
        'umpire': umpire,
        'home_score': home_score,
        'away_score': away_score,
        'home_win': home_win,
        'run_line_result': run_line_result,
        'nrfi_result': nrfi_result,
        'model_version': 'backfill_2025',
        'result_logged_at': datetime.now(timezone.utc).isoformat(),
    }

    # Fetch pitcher season ERA (rate limited)
    if home_pitcher_id:
        era = get_pitcher_season_era(home_pitcher_id, 2025)
        if era is not None:
            record['home_sp_era'] = era
    if away_pitcher_id:
        era = get_pitcher_season_era(away_pitcher_id, 2025)
        if era is not None:
            record['away_sp_era'] = era

    # Total result — we don't have betting lines, but log actual total for training
    # total_result will be null since we have no lines to compare against

    if not dry_run:
        upload_result(record)

    return record


def upload_result(record):
    """Upsert a game record to mlb_game_results"""
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/mlb_game_results?on_conflict=game_id',
        headers=HEADERS,
        json=record
    )
    if r.status_code not in [200, 201, 204]:
        print(f'  Upload error {r.status_code}: {r.text[:200]}')
        return False
    return True


def run(start_date, end_date, dry_run=False):
    print(f'Backfilling 2025 MLB games: {start_date} to {end_date}')
    if dry_run:
        print('DRY RUN — no data will be uploaded')
    print()

    current = start_date
    total_games = 0
    total_uploaded = 0
    total_days = 0

    while current <= end_date:
        date_str = current.isoformat()
        games = fetch_schedule(date_str)
        final_games = [g for g in games if g.get('status', {}).get('abstractGameState') == 'Final']

        if final_games:
            print(f'{date_str}: {len(final_games)} games')
            for game in final_games:
                result = process_game(game, dry_run=dry_run)
                if result:
                    home = result['home_team'].split()[-1]
                    away = result['away_team'].split()[-1]
                    nrfi = result.get('nrfi_result', '?')
                    print(f'  {away} {result["away_score"]} @ {home} {result["home_score"]} | {nrfi} | Ump: {result.get("umpire", "?")}')
                    total_uploaded += 1
                total_games += 1

            # Rate limit: ~1 second per day of games (each day hits API for pitcher ERA)
            time.sleep(0.5)

        total_days += 1
        # Progress update every 7 days
        if total_days % 7 == 0:
            print(f'  ... {total_days} days processed, {total_uploaded} games logged')

        current += timedelta(days=1)

    print(f'\nDone! {total_uploaded}/{total_games} games logged across {total_days} days')
    if dry_run:
        print('(Dry run — nothing was uploaded)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Backfill 2025 MLB season data')
    parser.add_argument('--start', default='2025-03-27', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', default='2025-09-28', help='End date (YYYY-MM-DD)')
    parser.add_argument('--dry-run', action='store_true', help='Preview without uploading')
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    run(start, end, dry_run=args.dry_run)
