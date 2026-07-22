"""NFL cohort backfill — 10 audit cohorts from 4-season baselines.

Reads graded nfl_game_results, computes cohort membership + outcomes,
writes hit rates to mlb_tier_calibration (shared table with sport='nfl').

Cohorts (from project_nfl_phase1_audit_baselines_507):
  nfl_heavy_home_dog       - home spread >= +7   (65.4% ⭐)
  nfl_outdoor_under        - outdoor + (cold or wind)  (52.1%)
  nfl_div_home_cover       - division game, home team  (48.6%)
  nfl_dome_over            - dome + total >= 47  (~51%)
  nfl_prime_time           - Thu/Sun/Mon night   (~50%)
  nfl_short_week           - rest_days <= 4      (~50%)
  nfl_long_rest_favorite   - rest_days >= 10 + favored  (~50%)
  nfl_qb_road_start        - QB first road start (needs player data)
  nfl_west_coast_1pm_et    - West coast team playing 1pm ET  (travel angle)
  nfl_home_fav_cover       - home team favored + covered  (50.1%)

USAGE:
    python nfl_cohort_backfill.py              # all seasons
    python nfl_cohort_backfill.py --season 2025
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _f(v) -> Optional[float]:
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v) -> Optional[int]:
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


NFL_DIVISION = {
    # AFC East
    'BUF': 'AFC East', 'MIA': 'AFC East', 'NE': 'AFC East', 'NYJ': 'AFC East',
    # AFC North
    'BAL': 'AFC North', 'CIN': 'AFC North', 'CLE': 'AFC North', 'PIT': 'AFC North',
    # AFC South
    'HOU': 'AFC South', 'IND': 'AFC South', 'JAX': 'AFC South', 'TEN': 'AFC South',
    # AFC West
    'DEN': 'AFC West', 'KC': 'AFC West', 'LV': 'AFC West', 'LAC': 'AFC West',
    # NFC East
    'DAL': 'NFC East', 'NYG': 'NFC East', 'PHI': 'NFC East', 'WAS': 'NFC East',
    # NFC North
    'CHI': 'NFC North', 'DET': 'NFC North', 'GB': 'NFC North', 'MIN': 'NFC North',
    # NFC South
    'ATL': 'NFC South', 'CAR': 'NFC South', 'NO': 'NFC South', 'TB': 'NFC South',
    # NFC West
    'ARI': 'NFC West', 'LA': 'NFC West', 'SF': 'NFC West', 'SEA': 'NFC West',
}

WEST_COAST_TEAMS = {'LA', 'LAC', 'SF', 'SEA'}


def fetch_games(season_filter: Optional[int] = None) -> list:
    """Pull all graded NFL games."""
    filters = ['home_score=not.is.null']
    if season_filter:
        filters.append(f'season=eq.{season_filter}')
    url = (f'{SB}/rest/v1/nfl_game_results?'
           f'{"&".join(filters)}&select=*&limit=5000')
    r = requests.get(url, headers=H_READ, timeout=30)
    return r.json() if r.status_code == 200 else []


def compute_cohorts_for_game(g: dict) -> list[str]:
    """Return list of cohort keys this game belongs to."""
    cohorts = []
    home = g.get('home_team'); away = g.get('away_team')
    spread = _f(g.get('close_spread'))
    total = _f(g.get('close_total'))
    home_score = _i(g.get('home_score'))
    away_score = _i(g.get('away_score'))
    if home_score is None or away_score is None:
        return []

    # SPREAD CONVENTION (nflverse standard): close_spread is home team's
    # spread. POSITIVE = home team is favored. See
    # nfl_backfill_results.py:108-120 for verification (SB LVIII, W1 2024,
    # W1 2025 examples). This differs from Odds API native — nfl_odds_pull
    # flips at write time to match.

    # 1. Heavy home dog — home is +7 or more UNDERDOG → spread <= -7
    if spread is not None and spread <= -7.0:
        cohorts.append('nfl_heavy_home_dog')

    # 2. Outdoor under — outdoor + (cold or wind)
    roof = (g.get('roof') or '').lower()
    temp = _f(g.get('temp'))
    wind = _f(g.get('wind'))
    if roof in ('outdoors', 'open') and (
        (temp is not None and temp <= 40) or (wind is not None and wind >= 12)
    ):
        cohorts.append('nfl_outdoor_under')

    # 3. Division game — home team POV
    if home and away and NFL_DIVISION.get(home) == NFL_DIVISION.get(away) \
       and NFL_DIVISION.get(home):
        cohorts.append('nfl_div_home_cover')

    # 4. Dome over — indoor + high total
    if roof in ('dome', 'closed') and total is not None and total >= 47:
        cohorts.append('nfl_dome_over')

    # (spread convention: nflverse positive = home fav)
    # Home-fav / long-rest / heavy-home-fav sign fixes:
    #   spread > 0 → home favored
    #   spread < 0 → home underdog

    # 5. Prime-time (weekday flag)
    weekday = g.get('weekday') or ''  # nflverse gives 'Sunday','Monday','Thursday'
    if weekday in ('Thursday', 'Monday') or (
        weekday == 'Sunday' and str(g.get('gametime', '')) >= '20:00'
    ):
        cohorts.append('nfl_prime_time')

    # 6. Short week — rest_days from either side
    home_rest = _i(g.get('home_rest'))
    away_rest = _i(g.get('away_rest'))
    if (home_rest is not None and home_rest <= 4) or \
       (away_rest is not None and away_rest <= 4):
        cohorts.append('nfl_short_week')

    # 7. Long rest favorite (nflverse: spread > 0 = home fav)
    if spread is not None:
        home_fav = spread > 0
        away_fav = spread < 0
        if home_fav and home_rest is not None and home_rest >= 10:
            cohorts.append('nfl_long_rest_favorite')
        elif away_fav and away_rest is not None and away_rest >= 10:
            cohorts.append('nfl_long_rest_favorite')

    # 8. West coast 1pm ET travel
    if away in WEST_COAST_TEAMS and str(g.get('gametime', '')).startswith('13:'):
        cohorts.append('nfl_west_coast_1pm_et')

    # 9. Home favored (nflverse: spread > 0 = home fav)
    if spread is not None and spread > 0:
        cohorts.append('nfl_home_fav')

    return cohorts


def cohort_result(cohort: str, g: dict) -> Optional[str]:
    """Was the cohort's implied bet a 'Win' | 'Loss' | 'Push'?

    Returns None if outcome can't be computed for this cohort.
    """
    home_score = _i(g.get('home_score'))
    away_score = _i(g.get('away_score'))
    spread = _f(g.get('close_spread'))
    total = _f(g.get('close_total'))
    if home_score is None or away_score is None:
        return None
    margin = home_score - away_score  # positive = home wins by margin
    total_pts = home_score + away_score

    # Cohorts whose bet is a home spread cover
    home_spread_cohorts = {
        'nfl_heavy_home_dog',
        'nfl_div_home_cover',
        'nfl_home_fav',
    }
    # Cohorts whose bet is an over on the total
    total_over_cohorts = {'nfl_dome_over'}
    # Cohorts whose bet is an under on the total
    total_under_cohorts = {'nfl_outdoor_under'}
    # Cohorts that are informational (no direct pick) — return None
    info_only = {'nfl_prime_time', 'nfl_short_week',
                 'nfl_long_rest_favorite', 'nfl_west_coast_1pm_et'}

    if cohort in home_spread_cohorts and spread is not None:
        # nflverse convention: spread > 0 = home fav. Home covers when
        # margin > close_spread (see nfl_backfill_results.py:108-120).
        if margin > spread: return 'Win'
        if margin < spread: return 'Loss'
        return 'Push'
    if cohort in total_over_cohorts and total is not None:
        if total_pts > total: return 'Win'
        if total_pts < total: return 'Loss'
        return 'Push'
    if cohort in total_under_cohorts and total is not None:
        if total_pts < total: return 'Win'
        if total_pts > total: return 'Loss'
        return 'Push'
    if cohort in info_only:
        return None
    return None


def build_calibration_rows(cohort_tallies: dict) -> list:
    """Convert cohort tallies to mlb_tier_calibration rows."""
    from datetime import date
    today = date.today().isoformat()
    rows = []
    for cohort, tally in cohort_tallies.items():
        n = tally['W'] + tally['L']
        if n == 0:
            continue
        pct = round(100.0 * tally['W'] / n, 1)
        rows.append({
            'tier': cohort.upper().replace('NFL_', ''),  # tier column stores cohort tag
            'sport': 'nfl',
            'window_label': 'lifetime',
            'wins': tally['W'],
            'losses': tally['L'],
            'pushes': tally['P'],
            'hit_pct': pct,
            'sample_n': n,
            'computed_date': today,
            'notes': f'cohort={cohort}',
        })
    return rows


def upsert_calibration(rows: list, dry_run: bool = False) -> int:
    if not rows:
        return 0
    if dry_run:
        for r in rows:
            print(f"  [DRY] {r['tier']:24} {r['hit_pct']:5.1f}% ({r['wins']}-{r['losses']}, n={r['sample_n']})")
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/mlb_tier_calibration'
        f'?on_conflict=tier,sport,window_label,computed_date',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(season_filter: Optional[int] = None, dry_run: bool = False) -> None:
    print(f'=== NFL cohort backfill ===')
    games = fetch_games(season_filter)
    if not games:
        print('  ✗ no games found — run nfl_backfill_results.py first')
        return
    print(f'  games loaded: {len(games)}')

    cohort_tallies = defaultdict(lambda: {'W': 0, 'L': 0, 'P': 0})
    for g in games:
        cohorts = compute_cohorts_for_game(g)
        for c in cohorts:
            result = cohort_result(c, g)
            if result:
                cohort_tallies[c][result[0]] += 1  # 'W' | 'L' | 'P'

    print(f'  cohorts computed: {len(cohort_tallies)}')
    rows = build_calibration_rows(cohort_tallies)
    print(f'  calibration rows: {len(rows)}')

    written = upsert_calibration(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} calibration rows to mlb_tier_calibration')

    # Summary sorted by hit rate
    print(f'\n=== Sorted cohort hit rates ===')
    for r in sorted(rows, key=lambda x: -x['hit_pct']):
        star = ' ⭐' if r['hit_pct'] >= 60 else ''
        print(f"  {r['tier']:24} {r['hit_pct']:5.1f}%  (n={r['sample_n']:>3}){star}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=None,
                    help='Compute for a specific season only (default: all)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(season_filter=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
