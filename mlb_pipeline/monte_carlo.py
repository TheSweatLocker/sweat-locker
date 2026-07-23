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


def _pitcher_vs_team_mult(recent_era, recent_n_starts, season_era=None, season_ip=None):
    """Pitcher-vs-team mastery multiplier (2026-07-23, updated to dampen
    by sample size after MC v2 ablation showed 0.00 delta from gate
    blocking too many games).

    Priority: recent (last 3 starts vs opp) → career (5-season aggregate).
    Both paths already source-side gated (6-IP recent, 15-IP career) so
    era is real signal, not 1-inning noise.

    SAMPLE-DAMPENED effect: full mastery signal only fires when we have
    lots of data. Small samples (3 recent starts / 15 career IP) get
    partial effect (blend with neutral 1.0). This lets us USE the data
    without over-swinging on small samples.

      recent path:  full effect at n_starts>=5, 50% at 3-4, none <3
      career path:  full effect at ip>=30, 50% at 15-29, none <15

    Overall clamp 0.80-1.20.

    Direction: lower ERA vs this team = stronger mastery = LOWER runs mult.
    """
    era = None
    weight = 0.0
    if recent_era is not None and recent_n_starts:
        try:
            n = int(recent_n_starts)
            if n >= 5:
                era, weight = recent_era, 1.0
            elif n >= 3:
                era, weight = recent_era, 0.5
        except (TypeError, ValueError):
            pass
    # Career fallback (only when recent not usable). Note: `get_pitcher_vs_team`
    # already gates at 15 IP source-side and returns None below that — so
    # season_era existing at ALL implies IP≥15 was already checked. Historical
    # mlb_game_results copies era but drops ip (data pipeline gap 2026-07-23);
    # trust era-alone as "yes, has sample" when era populated.
    if era is None and season_era is not None:
        try:
            if season_ip is not None:
                ip = float(season_ip)
                weight = 1.0 if ip >= 30 else (0.5 if ip >= 15 else 0.0)
            else:
                # era populated but ip null (historical pipeline gap) —
                # default to 0.5 weight since era was gated at 15 IP source-side.
                weight = 0.5
            if weight > 0.0:
                era = season_era
        except (TypeError, ValueError):
            pass
    if era is None or weight == 0.0:
        return 1.0
    try:
        raw = float(era) / LEAGUE_AVG_XERA
        # Blend: at weight=1.0, full raw effect. At weight=0.5, half effect
        # (blend halfway to neutral 1.0).
        blended = 1.0 + weight * (raw - 1.0)
        return _clamp(blended, 0.80, 1.20)
    except (TypeError, ValueError):
        return 1.0


def _umpire_over_mult(umpire_note):
    """Umpire tendency multiplier from stored umpire_note text.

    umpire_note format examples:
      "Chris Segal — neutral zone, 49% over rate"
      "Angel Hernandez — tight zone, 42% over rate"
      "Doug Eddings — wide zone, 55% over rate"

    Extracts the "X% over rate" number. League-neutral = 50%.
    Direction: >50% = ump favors OVER = higher scoring multiplier.

    Clamped 0.92-1.08 — small effect (K/BB tendencies feed into MC's
    xERA input indirectly already; this is the *residual* umpire adjust).
    Applies to full game, not just first inning.
    """
    if not umpire_note:
        return 1.0
    import re
    m = re.search(r'(\d+)\s*%\s*over', str(umpire_note).lower())
    if not m:
        return 1.0
    try:
        pct = int(m.group(1)) / 100.0
        # 50% = 1.0. Each 1% over league-neutral = +0.4% run rate,
        # capped at ±8% total (roughly matches the empirical spread from
        # 42%-lo umps to 58%-hi umps in the 30d cohort).
        raw = 1.0 + (pct - 0.50) * 0.4
        return _clamp(raw, 0.92, 1.08)
    except (TypeError, ValueError):
        return 1.0


def _defense_mult(oaa, catcher_framing):
    """Defensive quality multiplier — OAA (positioning/range) +
    catcher framing (extra called strikes).

    OAA: 0 = neutral. Elite +15 → 0.96x runs. Bad -15 → 1.04x.
    Framing: 0 = neutral. Elite +8 → 0.98x. Bad -8 → 1.02x.

    Both clamped small — MC scorer suppression already largely handled
    by pitcher xERA, so this is *residual* defense signal only.
    Clamped 0.92-1.08 combined.
    """
    mult = 1.0
    if oaa is not None:
        try:
            oaa_f = float(oaa)
            mult *= _clamp(1.0 - oaa_f * 0.003, 0.94, 1.06)
        except (TypeError, ValueError):
            pass
    if catcher_framing is not None:
        try:
            cf_f = float(catcher_framing)
            mult *= _clamp(1.0 - cf_f * 0.003, 0.96, 1.04)
        except (TypeError, ValueError):
            pass
    return _clamp(mult, 0.92, 1.08)


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

    # v2 multipliers (2026-07-23) — all default to 1.0 when data missing.
    # SP-vs-team mastery: only applies while starter is in box. Attached to
    # the defending side's SP (his mastery against the scoring team).
    m = _pitcher_vs_team_mult(
        defending.get('sp_vs_team_recent_era'),
        defending.get('sp_vs_team_recent_n'),
        defending.get('sp_vs_team_season_era'),
        defending.get('sp_vs_team_season_ip'),
    ) if box == 'sp' else 1.0
    # Umpire adjustment: applies to every half-inning (shared, per game)
    u = _umpire_over_mult(scoring.get('umpire_note'))
    # Defense (OAA + framing) — attached to the defending team's fielders
    df = _defense_mult(defending.get('oaa'), defending.get('catcher_framing'))

    rate = base * f * g * d * p * park * w * m * u * df
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
    """Build per-side input dicts from mlb_game_context row.

    2026-07-23 additions: pitcher_vs_team mastery, umpire over-rate,
    catcher framing, team OAA. All fall back to neutral (1.0 mult) when
    missing so existing games without these fields aren't affected.
    """
    # Umpire is shared between both sides (whole game)
    ump_note = g.get('umpire_note')
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
        # 2026-07-23: pitcher-vs-team mastery (attached to the pitcher's own side)
        'sp_vs_team_recent_era': _f(g.get('home_pitcher_vs_team_recent_era')),
        'sp_vs_team_recent_n': _f(g.get('home_pitcher_vs_team_recent_n_starts')),
        'sp_vs_team_season_era': _f(g.get('home_pitcher_vs_team_era')),
        'sp_vs_team_season_ip': _f(g.get('home_pitcher_vs_team_ip')),
        # Defensive support (attached to fielding side)
        'oaa': _f(g.get('home_team_oaa')),
        'catcher_framing': _f(g.get('home_catcher_framing')),
        # Umpire — shared
        'umpire_note': ump_note,
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
        'sp_vs_team_recent_era': _f(g.get('away_pitcher_vs_team_recent_era')),
        'sp_vs_team_recent_n': _f(g.get('away_pitcher_vs_team_recent_n_starts')),
        'sp_vs_team_season_era': _f(g.get('away_pitcher_vs_team_era')),
        'sp_vs_team_season_ip': _f(g.get('away_pitcher_vs_team_ip')),
        'oaa': _f(g.get('away_team_oaa')),
        'catcher_framing': _f(g.get('away_catcher_framing')),
        'umpire_note': ump_note,
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
