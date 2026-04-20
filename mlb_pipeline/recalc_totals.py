"""
Retroactive recalculation of projected_total using v3 formula.

v3 uses xERA + bullpen weighted expected runs (same structure as spread model).
Old formula: home_rpg + away_rpg × park + weather + bullpen_adj (0.25)
New formula: league_avg × wrc × (0.6 × opp_xera/4.25 + 0.4 × opp_bp/4.25) × park

Usage:
  python mlb_pipeline/recalc_totals.py              # update all 2026
  python mlb_pipeline/recalc_totals.py --dry-run    # preview
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
STARTER_W = 0.6
BULLPEN_W = 0.4
LEAGUE_AVG_BP = 4.0


def recalc_total(g):
    """Apply v3 total formula. Returns (projected_total, over_lean)"""
    home_xera = g.get('home_sp_xera')
    away_xera = g.get('away_sp_xera')
    home_wrc = g.get('home_wrc_plus')
    away_wrc = g.get('away_wrc_plus')
    park = g.get('park_run_factor') or 100

    if not (home_xera and away_xera and home_wrc and away_wrc):
        return None, None

    try:
        h_xera = float(home_xera)
        a_xera = float(away_xera)
        h_wrc = float(home_wrc)
        a_wrc = float(away_wrc)
        park_f = float(park)
    except (TypeError, ValueError):
        return None, None

    if h_xera > 6.5 or a_xera > 6.5:
        return None, None

    h_bp = g.get('home_bullpen_era') or LEAGUE_AVG_BP
    a_bp = g.get('away_bullpen_era') or LEAGUE_AVG_BP
    try:
        h_bp = float(h_bp)
        a_bp = float(a_bp)
    except:
        h_bp = LEAGUE_AVG_BP
        a_bp = LEAGUE_AVG_BP

    park_mult = 1.0 + (park_f - 100) / 200

    # Base: team R/G × park
    h_rpg = g.get('home_runs_per_game')
    a_rpg = g.get('away_runs_per_game')
    if not (h_rpg and a_rpg):
        return None, None
    try:
        h_rpg = float(h_rpg)
        a_rpg = float(a_rpg)
    except:
        return None, None

    projected = (h_rpg + a_rpg) * park_mult

    # Weather
    temp = g.get('temperature')
    weather_adj = 0.25
    if temp is not None:
        try:
            t = float(temp)
            if t < 45: weather_adj -= 1.5
            elif t < 55: weather_adj -= 0.8
            elif t < 65: weather_adj -= 0.3
            elif t > 85: weather_adj += 0.8
            elif t > 75: weather_adj += 0.3
        except:
            pass
    projected += weather_adj

    # Small pitcher quality adjustment (combined xERA vs league avg)
    avg_xera = (h_xera + a_xera) / 2
    pitcher_adj = (avg_xera - 4.25) * 0.4
    projected += pitcher_adj

    # NRFI
    nrfi = g.get('nrfi_score')
    if nrfi:
        try:
            nrfi_adj = max(-1.0, min(1.0, (float(nrfi) - 50) * -0.02))
            projected += nrfi_adj
        except:
            pass

    # Bullpen (minimal weight based on audit)
    bp_adj = ((h_bp + a_bp) / 2 - 4.0) * 0.15
    projected += bp_adj

    projected = round(projected, 1)

    # Over lean
    total_line = g.get('close_total') or g.get('open_total')
    over_lean = None
    if total_line:
        try:
            delta = projected - float(total_line)
            over_lean = True if delta > 0.3 else False if delta < -2.0 else None
        except:
            pass
    return projected, over_lean


def fetch_games():
    all_games = []
    offset = 0
    while True:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_game_results'
            f'?season=eq.2026&select=game_id,game_date,home_team,away_team,'
            f'home_sp_xera,away_sp_xera,home_wrc_plus,away_wrc_plus,'
            f'home_runs_per_game,away_runs_per_game,'
            f'park_run_factor,home_bullpen_era,away_bullpen_era,'
            f'temperature,nrfi_score,close_total,open_total,'
            f'projected_total,over_lean,home_score,away_score'
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


def update_game(game_id, projected_total, over_lean):
    r = requests.patch(
        f'{SUPABASE_URL}/rest/v1/mlb_game_results?game_id=eq.{requests.utils.quote(game_id)}',
        headers=HEADERS,
        json={'projected_total': projected_total, 'over_lean': over_lean}
    )
    return r.status_code in (200, 204)


def run(dry_run=False):
    print('Fetching 2026 games...')
    games = fetch_games()
    print(f'  Loaded {len(games)} games')

    updated = 0
    skipped = 0

    for g in games:
        new_total, new_lean = recalc_total(g)
        if new_total is None:
            skipped += 1
            continue

        old_total = g.get('projected_total')

        if not dry_run:
            if update_game(g['game_id'], new_total, new_lean):
                updated += 1
                if updated <= 5 or updated % 50 == 0:
                    print(f'  {g["game_date"]} {g["away_team"]} @ {g["home_team"]}: '
                          f'{old_total} → {new_total}')
        else:
            updated += 1
            if updated <= 10:
                print(f'  [DRY] {g["game_date"]} {g["away_team"]} @ {g["home_team"]}: '
                      f'{old_total} → {new_total}')

    print(f'\n{"Would update" if dry_run else "Updated"}: {updated}')
    print(f'Skipped (missing inputs): {skipped}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(dry_run=args.dry_run)
