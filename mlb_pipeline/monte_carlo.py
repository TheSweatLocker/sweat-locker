"""
monte_carlo.py — Per-inning team-scoring Monte Carlo simulator.

Per-inning team scoring approach (per project_monte_carlo_design 2026-06-10).
Each inning, we sample team runs from a Poisson(λ) where λ is the team's
season R/G divided by 9, modulated by a chain of multiplicative adjustments:

  - pitcher_quality_mult     — better pitcher (lower xERA) suppresses runs
  - pitcher_recent_form      — L3 ERA blended with season xERA (SP only)
  - bullpen_gas_mult         — taxed BP allows more runs (BP innings only)
  - offense_drift_mult       — L10 R/G vs season (clamped 0.75-1.25)
  - hand_split_mult          — wRC+ vs opposing-hand split (clamped 0.80-1.30)
  - park_hr_mult             — Park HR factor, translated to ~33% all-runs effect
  - weather_mult             — Cold / wind in/out on HR conversion

Per game we run N=10,000 simulations and aggregate:
  - mu_total, sigma_total (run total mean + variance)
  - p_over, p_under against a line
  - p_home_win, p_away_win, expected_margin
  - p_nrfi, p_yrfi (1st inning scoring distribution)

Per founder feedback: park is DE-WEIGHTED (only via HR factor on 33% of runs)
because the empirical cohort backtest showed pure park rules barely cleared
LEAN tier. SP form / BP gas / offense drift / L10 carry the heaviest weight
in this model.
"""
import math
import random
from statistics import mean, stdev

LEAGUE_AVG_XERA = 4.50
LEAGUE_AVG_RPG = 4.5
DEFAULT_PROJECTED_OUTS = 18  # 6 IP


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v, low, high):
    return max(low, min(high, v))


def _pitcher_quality_mult(xera):
    """xera 4.50 → 1.0 (neutral). xera 3.00 → 0.67 (suppresses runs).
    xera 5.50 → 1.22 (allows more runs). Direction: BETTER pitcher (lower xERA)
    = LOWER multiplier on scoring rate."""
    if xera is None or xera <= 0:
        return 1.0
    return xera / LEAGUE_AVG_XERA


def _form_blend_mult(season_era, l3_era):
    """50/50 blend of season + L3 ERA. SP only — BP doesn't track L3 in our schema.
    Direction: lower ERA = lower multiplier (suppresses scoring)."""
    if season_era is None and l3_era is None:
        return 1.0
    if l3_era is None:
        return season_era / LEAGUE_AVG_XERA if season_era else 1.0
    if season_era is None:
        return l3_era / LEAGUE_AVG_XERA if l3_era else 1.0
    blended = 0.5 * season_era + 0.5 * l3_era
    return blended / LEAGUE_AVG_XERA if blended else 1.0


def _bullpen_gas_mult(bp_3d):
    """0-3 uses = rested (0.95x), 4-7 = normal (1.0x), 8+ = taxed (1.0 + 0.05 per use over 7)."""
    if bp_3d is None:
        return 1.0
    if bp_3d <= 3:
        return 0.95
    if bp_3d <= 7:
        return 1.0
    return min(1.20, 1.0 + 0.05 * (bp_3d - 7))


def _offense_drift_mult(team_l10_rpg, team_season_rpg):
    """Team's L10 R/G vs season pace. Clamped so single hot/cold streaks can't dominate."""
    if not team_l10_rpg or not team_season_rpg or team_season_rpg <= 0:
        return 1.0
    return _clamp(team_l10_rpg / team_season_rpg, 0.75, 1.25)


def _hand_split_mult(team_wrc_vs_hand, team_wrc_season):
    """Platoon-adjusted wRC+ vs season wRC+. Clamped 0.80-1.30 (extreme splits get tempered)."""
    if not team_wrc_vs_hand or not team_wrc_season or team_wrc_season <= 0:
        return 1.0
    return _clamp(team_wrc_vs_hand / team_wrc_season, 0.80, 1.30)


def _park_hr_mult(park_hr_factor):
    """Park HR factor 100 = neutral. Coors 115 → 1.15 HR rate.
    Translate to ~33% all-runs effect (HRs are roughly a third of MLB scoring).
    Coors 115: full-game multiplier = 1.0 + 0.33 * (1.15 - 1.0) = 1.050"""
    if not park_hr_factor:
        return 1.0
    park_normalized = park_hr_factor / 100.0
    return 1.0 + 0.33 * (park_normalized - 1.0)


def _weather_mult(temp, wind_speed, wind_direction, is_dome):
    """Weather effect on HR conversion (and thus indirectly on scoring rate)."""
    if is_dome:
        return 1.0
    mult = 1.0
    if temp is not None:
        if temp <= 50:
            mult *= 0.88  # cold suppresses HRs
        elif temp >= 85:
            mult *= 1.05
    wd = (wind_direction or '').upper()
    if wind_speed and wind_speed >= 10:
        if any(d in wd for d in ('S', 'SW', 'SE', 'OUT')):
            mult *= 1.10
        elif any(d in wd for d in ('N', 'NW', 'NE', 'IN')):
            mult *= 0.85
    return mult


def _which_pitcher_inning(inning, projected_outs):
    """Return 'sp' / 'bp_setup' / 'bp_closer' for the pitcher in box."""
    if not projected_outs:
        projected_outs = DEFAULT_PROJECTED_OUTS
    sp_innings = projected_outs / 3.0
    if inning <= sp_innings:
        return 'sp'
    if inning >= 9:
        return 'bp_closer'
    return 'bp_setup'


def _inning_base_rate(team_season_rpg):
    """Base runs/inning derived from team's season R/G."""
    rpg = team_season_rpg if team_season_rpg else LEAGUE_AVG_RPG
    return rpg / 9.0


def _simulate_inning(rng, scoring, defending, inning):
    """Simulate one half-inning. Returns runs scored by `scoring` side."""
    base = _inning_base_rate(scoring['season_rpg'])

    # Which pitcher is in box for the defending side
    box = _which_pitcher_inning(inning, defending['projected_outs'])
    if box == 'sp':
        pitcher_xera = defending['sp_xera']
        pitcher_l3 = defending['sp_l3_era']
    else:
        # BP — closer typically slightly better than middle relief but our
        # schema only carries one bp_era number, so we use it for both.
        pitcher_xera = defending['bp_era']
        pitcher_l3 = None

    # Adjustments (chain multiplicatively). One pitcher_form multiplier (SP uses
    # season+L3 blend, BP uses bp_era only). Don't double-count pitcher quality
    # by applying both quality AND form on the same xera.
    f = _form_blend_mult(pitcher_xera, pitcher_l3)
    g = _bullpen_gas_mult(defending['bp_3d']) if box != 'sp' else 1.0
    d = _offense_drift_mult(scoring['l10_rpg'], scoring['season_rpg'])
    p = _hand_split_mult(scoring['wrc_vs_hand'], scoring['wrc_season'])
    park = _park_hr_mult(scoring['park_hr_factor'])
    w = _weather_mult(scoring['temp'], scoring['wind_speed'],
                      scoring['wind_direction'], scoring['is_dome'])

    rate = base * f * g * d * p * park * w
    rate = _clamp(rate, 0.05, 3.0)

    # Poisson sample (Knuth's algorithm — fine for λ < 30)
    L = math.exp(-rate)
    k = 0
    pk = 1.0
    while True:
        k += 1
        pk *= rng.random()
        if pk <= L:
            return k - 1


def _extract_sides(g):
    """Build per-side input dicts from mlb_game_context row."""
    home = {
        'sp_xera': _f(g.get('home_sp_xera')),
        'sp_l3_era': _f(g.get('home_pitcher_last_3_era')),
        'bp_era': _f(g.get('home_bullpen_era')) or 4.50,
        'bp_3d': _f(g.get('home_bp_relievers_3d')) or 5,
        'projected_outs': _f(g.get('home_pitcher_projected_outs')) or DEFAULT_PROJECTED_OUTS,
        'season_rpg': _f(g.get('home_runs_per_game')) or LEAGUE_AVG_RPG,
        'l10_rpg': _f(g.get('home_last10_runs_per_game')),
        'wrc_season': _f(g.get('home_wrc_plus')) or 100,
        'wrc_vs_hand': _f(g.get('home_wrc_vs_opp_hand')),
        'park_hr_factor': _f(g.get('park_hr_factor')) or 100,
        'temp': _f(g.get('temperature')),
        'wind_speed': _f(g.get('wind_speed')),
        'wind_direction': g.get('wind_direction') or '',
        'is_dome': bool(g.get('is_dome')),
    }
    away = {
        'sp_xera': _f(g.get('away_sp_xera')),
        'sp_l3_era': _f(g.get('away_pitcher_last_3_era')),
        'bp_era': _f(g.get('away_bullpen_era')) or 4.50,
        'bp_3d': _f(g.get('away_bp_relievers_3d')) or 5,
        'projected_outs': _f(g.get('away_pitcher_projected_outs')) or DEFAULT_PROJECTED_OUTS,
        'season_rpg': _f(g.get('away_runs_per_game')) or LEAGUE_AVG_RPG,
        'l10_rpg': _f(g.get('away_last10_runs_per_game')),
        'wrc_season': _f(g.get('away_wrc_plus')) or 100,
        'wrc_vs_hand': _f(g.get('away_wrc_vs_opp_hand')),
        'park_hr_factor': _f(g.get('park_hr_factor')) or 100,
        'temp': _f(g.get('temperature')),
        'wind_speed': _f(g.get('wind_speed')),
        'wind_direction': g.get('wind_direction') or '',
        'is_dome': bool(g.get('is_dome')),
    }
    return home, away


def simulate_game(g, n_iter=10000, line=None, seed=None):
    """Run n_iter Monte Carlo simulations of the game.

    Args:
        g: dict-like mlb_game_context row with home/away SP/BP/lineup/park fields
        n_iter: simulation count (default 10000)
        line: total to compute P(OVER) — defaults to g.close_total or g.open_total
        seed: optional random seed for reproducible backtests

    Returns:
        dict — mu_total, sigma_total, p_over, p_under, p_home_win, p_away_win,
                p_nrfi, p_yrfi, expected_margin, etc. or None if inputs missing.
    """
    # Require both SPs to be set (per founder spec: skip games where SPs unannounced)
    if not g.get('home_pitcher') or not g.get('away_pitcher'):
        return None

    home, away = _extract_sides(g)
    rng = random.Random(seed) if seed is not None else random.Random()

    if line is None:
        line = _f(g.get('close_total')) or _f(g.get('open_total'))

    totals = []
    home_scores = []
    away_scores = []
    home_wins = 0
    away_wins = 0
    nrfis = 0
    yrfis = 0
    over_count = 0
    margin_sum = 0.0

    for _ in range(n_iter):
        home_score = 0
        away_score = 0
        first_inning_runs = 0
        for inning in range(1, 10):
            # Top half: away bats vs home pitcher
            top_runs = _simulate_inning(rng, away, home, inning)
            away_score += top_runs
            if inning == 1:
                first_inning_runs += top_runs
            # Bottom half: home bats vs away pitcher. Skip bottom 9th if home
            # leads going in (walk-off short-circuit).
            if inning < 9 or away_score >= home_score:
                bot_runs = _simulate_inning(rng, home, away, inning)
                home_score += bot_runs
                if inning == 1:
                    first_inning_runs += bot_runs

        total = home_score + away_score
        totals.append(total)
        home_scores.append(home_score)
        away_scores.append(away_score)
        if home_score > away_score:
            home_wins += 1
        elif away_score > home_score:
            away_wins += 1
        margin_sum += (home_score - away_score)
        if first_inning_runs == 0:
            nrfis += 1
        else:
            yrfis += 1
        if line is not None and total > line:
            over_count += 1

    mu = mean(totals)
    sigma = stdev(totals) if len(totals) > 1 else 0.0
    return {
        'mu_total': round(mu, 2),
        'sigma_total': round(sigma, 2),
        'mu_home': round(mean(home_scores), 2),
        'mu_away': round(mean(away_scores), 2),
        'p_over': round(over_count / n_iter, 3) if line is not None else None,
        'p_under': round(1 - over_count / n_iter, 3) if line is not None else None,
        'p_home_win': round(home_wins / n_iter, 3),
        'p_away_win': round(away_wins / n_iter, 3),
        'p_nrfi': round(nrfis / n_iter, 3),
        'p_yrfi': round(yrfis / n_iter, 3),
        'expected_margin': round(margin_sum / n_iter, 2),
        'line_used': line,
        'n_iter': n_iter,
    }


if __name__ == '__main__':
    # Smoke test: pull today's games and run MC on each
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    import os
    import requests
    sys.stdout.reconfigure(encoding='utf-8')
    from datetime import datetime, timezone, timedelta
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    today = et_now.strftime('%Y-%m-%d')

    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    r = requests.get(
        f'{url}/rest/v1/mlb_game_context',
        headers={'apikey': key, 'Authorization': f'Bearer {key}'},
        params=[('select', '*'), ('game_date', f'eq.{today}')],
    )
    print(f"Monte Carlo — {today} smoke test (10K iterations / game, seed=42)")
    print(f"{'GAME':<30}{'μ TOT':>7}{'σ':>6}{'LINE':>7}{'P(O)':>7}{'P(home)':>9}{'P(NRFI)':>10}")
    print('-' * 80)
    for g in r.json():
        away = (g.get('away_team') or '?')[:14]
        home = (g.get('home_team') or '?')[:14]
        res = simulate_game(g, n_iter=10000, seed=42)
        if res is None:
            print(f"{away}@{home:<15} (skipped — SP not announced)")
            continue
        line = res['line_used']
        po = f"{res['p_over']:.3f}" if res['p_over'] is not None else '-'
        print(f"{away}@{home:<15}{res['mu_total']:>7.2f}{res['sigma_total']:>6.2f}{line!s:>7}{po:>7}{res['p_home_win']:>9.3f}{res['p_nrfi']:>10.3f}")
