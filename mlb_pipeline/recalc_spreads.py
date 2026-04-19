"""
Retroactive recalculation of projected_spread and spread_delta for all 2026 games.

Applies the v3 formula (starter 60% + bullpen 40%) to all historical games
using their stored inputs. Updates mlb_game_results in Supabase.

Usage:
  python mlb_pipeline/recalc_spreads.py              # update all 2026
  python mlb_pipeline/recalc_spreads.py --dry-run    # preview changes
  python mlb_pipeline/recalc_spreads.py --season 2025  # backfill 2025
"""
import os
import argparse
import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal',
}

LEAGUE_AVG_RPG = 4.25
STARTER_WEIGHT = 0.6
BULLPEN_WEIGHT = 0.4
LEAGUE_AVG_BP = 4.0


def recalculate_spread(g):
    """Apply v3 formula to a game. Returns (projected_spread, spread_delta, spread_lean)"""
    # Required inputs
    home_xera = g.get('home_sp_xera')
    away_xera = g.get('away_sp_xera')
    home_wrc = g.get('home_wrc_plus')
    away_wrc = g.get('away_wrc_plus')
    park = g.get('park_run_factor') or 100

    if not (home_xera and away_xera and home_wrc and away_wrc):
        return None, None, None

    # Sanitize xERA >6.5 (bad early season data)
    try:
        home_xera_f = float(home_xera)
        away_xera_f = float(away_xera)
        home_wrc_f = float(home_wrc)
        away_wrc_f = float(away_wrc)
        park_f = float(park)
    except (TypeError, ValueError):
        return None, None, None

    if home_xera_f > 6.5 or away_xera_f > 6.5:
        return None, None, None

    # Use league avg if bullpen missing
    home_bp = g.get('home_bullpen_era') or LEAGUE_AVG_BP
    away_bp = g.get('away_bullpen_era') or LEAGUE_AVG_BP
    try:
        home_bp = float(home_bp)
        away_bp = float(away_bp)
    except:
        home_bp = LEAGUE_AVG_BP
        away_bp = LEAGUE_AVG_BP

    park_mult = 1.0 + (park_f - 100) / 200

    home_factor = STARTER_WEIGHT * (away_xera_f / 4.25) + BULLPEN_WEIGHT * (away_bp / 4.25)
    away_factor = STARTER_WEIGHT * (home_xera_f / 4.25) + BULLPEN_WEIGHT * (home_bp / 4.25)

    home_expected = LEAGUE_AVG_RPG * (home_wrc_f / 100) * home_factor * park_mult
    away_expected = LEAGUE_AVG_RPG * (away_wrc_f / 100) * away_factor * park_mult

    projected_spread = round(home_expected - away_expected, 2)

    # Spread delta: model - posted (matching original game_context.py logic)
    # close_spread is home team's posted line (-1.5 = home favored)
    # projected_spread is model's home advantage (positive = home winning by X)
    # Convention match: close_spread -1.5 means home "ahead" by 1.5 in our comparison
    close_spread = g.get('close_spread')
    if close_spread is None:
        close_spread = g.get('open_spread')
    spread_delta = None
    if close_spread is not None:
        try:
            spread_delta = round(projected_spread - float(close_spread), 2)
        except:
            pass

    if projected_spread >= 0.5:
        spread_lean = 'home'
    elif projected_spread <= -0.5:
        spread_lean = 'away'
    else:
        spread_lean = None

    return projected_spread, spread_delta, spread_lean


def fetch_games(season):
    all_games = []
    offset = 0
    while True:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_game_results'
            f'?season=eq.{season}'
            f'&select=game_id,game_date,home_team,away_team,home_sp_xera,away_sp_xera,'
            f'home_wrc_plus,away_wrc_plus,park_run_factor,home_bullpen_era,away_bullpen_era,'
            f'close_spread,open_spread,projected_spread,spread_delta,spread_lean'
            f'&limit=1000&offset={offset}',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
        )
        batch = r.json()
        if not batch or not isinstance(batch, list):
            break
        all_games.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return all_games


def update_game(game_id, projected_spread, spread_delta, spread_lean):
    payload = {
        'projected_spread': projected_spread,
        'spread_delta': spread_delta,
        'spread_lean': spread_lean,
    }
    r = requests.patch(
        f'{SUPABASE_URL}/rest/v1/mlb_game_results?game_id=eq.{requests.utils.quote(game_id)}',
        headers=HEADERS,
        json=payload
    )
    return r.status_code in (200, 204)


def run(season, dry_run=False):
    print(f'Fetching {season} games...')
    games = fetch_games(season)
    print(f'  Loaded {len(games)} games')

    updated = 0
    skipped_missing_inputs = 0
    skipped_bad_xera = 0
    unchanged = 0

    for g in games:
        new_spread, new_delta, new_lean = recalculate_spread(g)

        if new_spread is None:
            # Check why it was skipped
            if not (g.get('home_sp_xera') and g.get('away_sp_xera') and
                    g.get('home_wrc_plus') and g.get('away_wrc_plus')):
                skipped_missing_inputs += 1
            else:
                skipped_bad_xera += 1
            continue

        old_spread = g.get('projected_spread')
        old_delta = g.get('spread_delta')

        if (old_spread is not None and abs(float(old_spread) - new_spread) < 0.01 and
            old_delta is not None and new_delta is not None and abs(float(old_delta) - new_delta) < 0.01):
            unchanged += 1
            continue

        if not dry_run:
            if update_game(g['game_id'], new_spread, new_delta, new_lean):
                updated += 1
                if updated <= 5 or updated % 50 == 0:
                    print(f'  {g["game_date"]} {g["away_team"]} @ {g["home_team"]}: '
                          f'spread {old_spread}→{new_spread} | delta {old_delta}→{new_delta}')
        else:
            updated += 1
            if updated <= 10:
                print(f'  [DRY] {g["game_date"]} {g["away_team"]} @ {g["home_team"]}: '
                      f'spread {old_spread}→{new_spread} | delta {old_delta}→{new_delta}')

    print(f'\nResults:')
    print(f'  {"Would update" if dry_run else "Updated"}: {updated}')
    print(f'  Unchanged: {unchanged}')
    print(f'  Skipped (missing inputs): {skipped_missing_inputs}')
    print(f'  Skipped (xERA > 6.5): {skipped_bad_xera}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--season', type=int, default=2026)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(args.season, dry_run=args.dry_run)
