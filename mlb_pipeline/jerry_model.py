"""The Jerry Model — deep-factor linear run projection.

A transparent weighted-formula projection per team that simulates the
game in three inning thirds (1-3, 4-6, 7-9) with mastery, recency-
weighted offense, home/away splits, bullpen gas penalties, park, and
weather factored in. All coefficients live in JERRY_WEIGHTS so they
can be tuned via backtest without restructuring the code.

Output per game:
    jerry_away_runs, jerry_home_runs  — per-team run projections
    jerry_total                       — sum
    jerry_spread                      — home - away (positive = home favored)
    components                        — per-team component breakdown for audit

Designed to run in shadow mode alongside v3/v4 for 2-4 weeks before
any user-facing surfacing. See _backtest_jerry.py for hit-rate vs
existing models on historical games.

Built 2026-05-30. Not yet calibrated — initial weights are educated
guesses pending backtest fit.
"""
from typing import Any, Dict, Optional, Tuple


# =============================================================================
# WEIGHT CONFIGURATION
# Every tunable coefficient lives here. Edit, re-run backtest, observe MAE/hit-
# rate delta. No code changes needed for weight tuning.
# =============================================================================
JERRY_WEIGHTS = {
    # --- Recency blends (alpha = weight on recent window vs season baseline) ---
    'offense_recency_alpha': 0.35,   # 35% L14 wRC+ proxy, 65% season
    'offense_l7_alpha': 0.20,        # 20% L7 OPS bump on top
    'starter_l3_alpha': 0.30,        # 30% L3 ERA, 70% season xERA

    # --- Starter component ---
    'starter_xera_weight': 0.55,     # base weight on xERA
    'starter_l3_weight': 0.30,       # L3 form weight
    'starter_first_inn_weight': 0.15, # 1st-inn ERA (used specifically for inning 1-3 bucket)
    'starter_ip_estimate': 5.5,      # innings the starter typically throws
    'starter_split_sensitivity': 0.10, # each +0.5 ERA worse in this split = +5% runs allowed

    # --- Mastery (vs current opp) adjustment ---
    'mastery_ip_gate': 15,           # below this IP, mastery doesn't fire (small sample)
    'mastery_strong_era': 2.50,      # mastery threshold
    'mastery_weak_era': 6.00,        # anti-mastery threshold
    'mastery_strong_multiplier': 0.85,  # opp runs × 0.85 when strong mastery
    'mastery_weak_multiplier': 1.18,    # opp runs × 1.18 when anti-mastery

    # --- Offense component ---
    'offense_wrc_baseline': 100,
    'offense_wrc_sensitivity': 0.012,  # each 10 wRC+ above 100 = +12% runs
    'offense_vs_hand_weight': 0.40,    # 40% vs-hand mix vs raw wRC+
    'offense_barrel_baseline': 8.0,    # league avg barrel%
    'offense_barrel_sensitivity': 0.015,  # each 1% above baseline = +1.5% runs
    'offense_xwoba_baseline': 0.320,
    'offense_xwoba_sensitivity': 1.5,    # each 0.010 above baseline = +1.5% runs

    # --- Team-specific home/away offensive splits ---
    # Uses runs_per_game_home / runs_per_game_away from mlb_team_offense
    # (per project_rest_and_lineup_signals — added 5/28). Multiplier =
    # split_rpg / season_rpg. Falls back to generic +4% home advantage
    # when split data missing.
    'offense_split_weight': 0.50,        # how strongly to apply the team-specific split (0.5 = halfway between raw season and full split)
    'offense_home_advantage_default': 1.04,  # fallback when split data missing

    # --- Inning bucket weights (per-third multipliers; normally 1.0) ---
    'bucket_1_3_weight': 1.0,
    'bucket_4_6_weight': 1.0,
    'bucket_7_9_weight': 1.0,

    # --- Bullpen ---
    'bullpen_base_era_weight': 1.0,
    'bullpen_gas_threshold': 6,      # relievers used L3D before penalty kicks in
    'bullpen_gas_penalty_per': 0.04, # +4% bullpen ERA per reliever over threshold
    'bullpen_gas_cap': 0.30,         # max 30% penalty (avoid blowups)

    # --- Inning-bucket transition assumptions ---
    'bucket_4_6_starter_share': 0.65,  # 65% of innings 4-6 still pitched by starter
    'bucket_4_6_bullpen_share': 0.35,

    # --- Park factor ---
    'park_baseline': 100,
    'park_sensitivity': 0.6,         # park 110 → +6% runs (not the naive +10%)

    # --- Weather ---
    'temp_baseline': 70,
    'temp_per_degree': 0.0030,       # each degree above 70 = +0.30% runs
    'wind_speed_threshold_out': 8,   # mph
    'wind_speed_threshold_in': 10,
    'wind_out_multiplier': 1.04,
    'wind_in_multiplier': 0.96,

    # --- Catcher framing (affects opposing offense) ---
    'framing_baseline': 0,
    'framing_per_unit': 0.008,       # each +1 framing = -0.8% opposing runs

    # --- Defense (OAA reduces opposing scoring) ---
    'oaa_baseline': 0,
    'oaa_per_unit': 0.003,           # each +1 OAA = -0.3% opposing scoring

    # --- Output bounds (sanity floor/ceiling) ---
    'min_team_runs': 1.0,
    'max_team_runs': 12.0,

    # --- Version stamp ---
    '_version': 'jerry_v1_initial',
}


def _f(v) -> Optional[float]:
    """Safe float coerce, returns None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _blend(weight_value_pairs):
    """Weighted average of (weight, value) pairs. Skips None values.
    Returns None if all values are None."""
    valid = [(w, v) for w, v in weight_value_pairs if v is not None]
    if not valid:
        return None
    total_w = sum(w for w, _ in valid)
    if total_w == 0:
        return None
    return sum(w * v for w, v in valid) / total_w


def _blend_wrc_plus(season, vs_hand, l14, w):
    """Blend wRC+ inputs into a single recency-and-platoon-adjusted number.
    Season is the baseline. vs_hand mixes in at offense_vs_hand_weight if
    available. L14 recency adds at offense_recency_alpha if available."""
    if season is None:
        return None
    blended = season
    if vs_hand is not None:
        vsw = w['offense_vs_hand_weight']
        blended = blended * (1 - vsw) + vs_hand * vsw
    if l14 is not None:
        a = w['offense_recency_alpha']
        blended = blended * (1 - a) + l14 * a
    return blended


def _starter_composite_rate(xera, l3_era, first_inn_era, w, for_first_inning=False):
    """Blend xERA + L3 ERA into a starter run rate (per 9 IP).
    When for_first_inning=True, include first_inning_era with extra weight."""
    if xera is None and l3_era is None and first_inn_era is None:
        return None
    if for_first_inning and first_inn_era is not None:
        pairs = [
            (w['starter_xera_weight'], xera),
            (w['starter_l3_weight'], l3_era),
            (w['starter_first_inn_weight'], first_inn_era),
        ]
    else:
        pairs = [
            (w['starter_xera_weight'], xera),
            (w['starter_l3_weight'], l3_era),
        ]
    return _blend(pairs)


def _mastery_multiplier(vs_team_era, vs_team_ip, w):
    """Multiplier applied to opposing-runs based on mastery / anti-mastery.
    Only fires when IP sample >= gate."""
    if vs_team_era is None or vs_team_ip is None or vs_team_ip < w['mastery_ip_gate']:
        return 1.0
    if vs_team_era <= w['mastery_strong_era']:
        return w['mastery_strong_multiplier']
    if vs_team_era >= w['mastery_weak_era']:
        return w['mastery_weak_multiplier']
    # Linear interpolation between thresholds
    span = w['mastery_weak_era'] - w['mastery_strong_era']
    pos = (vs_team_era - w['mastery_strong_era']) / span
    mult_span = w['mastery_weak_multiplier'] - w['mastery_strong_multiplier']
    return w['mastery_strong_multiplier'] + pos * mult_span


def _starter_split_multiplier(split_delta, w):
    """Pitcher home/away split adjustment.
    split_delta = pitcher_ERA_in_this_slot - pitcher_season_ERA.
    Positive = pitches worse in this slot → opp scores more."""
    if split_delta is None:
        return 1.0
    return 1.0 + split_delta * w['starter_split_sensitivity'] / 0.5  # +0.5 ERA → +sensitivity %


def _bullpen_gas_penalty(relievers_used_3d, w):
    """Each reliever used in L3D above threshold adds a penalty multiplier.
    Capped at gas_cap to avoid runaway projections."""
    if relievers_used_3d is None:
        return 0.0
    over = max(0, relievers_used_3d - w['bullpen_gas_threshold'])
    penalty = over * w['bullpen_gas_penalty_per']
    return min(penalty, w['bullpen_gas_cap'])


def _offense_quality_multiplier(wrc_blend, barrel_pct, xwoba, w):
    """Composite offensive-quality multiplier from wRC+ + barrel% + xwOBA."""
    mult = 1.0
    if wrc_blend is not None:
        mult *= 1.0 + (wrc_blend - w['offense_wrc_baseline']) * w['offense_wrc_sensitivity']
    if barrel_pct is not None:
        mult *= 1.0 + (barrel_pct - w['offense_barrel_baseline']) * w['offense_barrel_sensitivity']
    if xwoba is not None:
        mult *= 1.0 + (xwoba - w['offense_xwoba_baseline']) * w['offense_xwoba_sensitivity']
    return mult


def _bucket_runs(opp_rate_per_9, team_bucket_rpg, offense_mult, mastery_mult, split_mult, bucket_weight, w):
    """Project runs scored by this team in one inning third.
    opp_rate_per_9 = opposing pitcher's ERA-equivalent for this third
    team_bucket_rpg = this team's typical runs scored in this third (per game)
    """
    if opp_rate_per_9 is None and team_bucket_rpg is None:
        return 0.0
    # opp rate per 9 IP → per 3 IP (one third)
    opp_runs_per_third = (opp_rate_per_9 / 3.0) if opp_rate_per_9 is not None else (team_bucket_rpg or 0)
    # Blend opposing pitcher rate (60%) and team's typical scoring in this third (40%)
    if team_bucket_rpg is not None:
        base = 0.60 * opp_runs_per_third + 0.40 * team_bucket_rpg
    else:
        base = opp_runs_per_third
    runs = base * offense_mult * mastery_mult * split_mult * bucket_weight
    return max(0.0, runs)


def _project_team_runs(ctx: Dict[str, Any], team_side: str, w: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """Project runs scored by `team_side` team's offense against the opposing
    starter + bullpen. team_side ∈ {'away', 'home'}.

    Returns (final_runs, breakdown_dict).
    """
    opp_side = 'home' if team_side == 'away' else 'away'

    # === Opposing starter inputs ===
    opp_xera = _f(ctx.get(f'{opp_side}_sp_xera'))
    opp_l3_era = _f(ctx.get(f'{opp_side}_pitcher_last_3_era'))
    opp_first_inn = _f(ctx.get(f'{opp_side}_first_inning_era'))
    opp_split_delta = _f(ctx.get(f'{opp_side}_pitcher_split_delta'))
    opp_mastery_era = _f(ctx.get(f'{opp_side}_pitcher_vs_team_era'))
    opp_mastery_ip = _f(ctx.get(f'{opp_side}_pitcher_vs_team_ip'))

    # Inning bucket ERAs (enriched from mlb_pitcher_stats)
    opp_bucket_1_3 = _f(ctx.get(f'{opp_side}_innings_1_3_era'))
    opp_bucket_4_6 = _f(ctx.get(f'{opp_side}_innings_4_6_era'))

    # === Opposing bullpen ===
    opp_bullpen_era = _f(ctx.get(f'{opp_side}_bullpen_era'))
    opp_bullpen_7_9 = _f(ctx.get(f'{opp_side}_pitching_7_9_era')) or opp_bullpen_era
    opp_bullpen_gas = _f(ctx.get(f'{opp_side}_bp_relievers_3d'))

    # === This team's offense ===
    wrc_plus = _f(ctx.get(f'{team_side}_wrc_plus'))
    wrc_vs_hand = _f(ctx.get(f'{team_side}_wrc_vs_opp_hand'))
    wrc_l14 = _f(ctx.get(f'{team_side}_wrc_proxy_l14'))
    barrel = _f(ctx.get(f'{team_side}_team_barrel_pct'))
    xwoba = _f(ctx.get(f'{team_side}_team_xwoba'))

    # Inning bucket RPGs (enriched from mlb_team_offense)
    bucket_offense_1_3 = _f(ctx.get(f'{team_side}_innings_1_3_runs_per_game'))
    bucket_offense_4_6 = _f(ctx.get(f'{team_side}_innings_4_6_runs_per_game'))
    bucket_offense_7_9 = _f(ctx.get(f'{team_side}_innings_7_9_runs_per_game'))

    # === Opposing defense (reduces this team's scoring) ===
    opp_oaa = _f(ctx.get(f'{opp_side}_team_oaa'))
    opp_framing = _f(ctx.get(f'{opp_side}_catcher_framing'))

    # === Game-level ===
    park = _f(ctx.get('park_run_factor')) or w['park_baseline']
    temp = _f(ctx.get('temperature')) or w['temp_baseline']
    wind_speed = _f(ctx.get('wind_speed')) or 0
    wind_dir = (ctx.get('wind_direction') or '').upper()

    # === Team-specific home/away split (5/30 add per user request) ===
    # When mlb_team_offense provides per-team home/away R/G splits, blend
    # them in. Multiplier = split_rpg / season_rpg, then scaled by
    # offense_split_weight so the blend doesn't completely override the
    # other offensive signals.
    team_season_rpg = _f(ctx.get(f'{team_side}_runs_per_game_season')) or \
                      _f(ctx.get(f'{team_side}_runs_per_game'))
    team_split_rpg = _f(ctx.get(f'{team_side}_runs_per_game_{team_side}'))  # away→runs_per_game_away, home→runs_per_game_home
    split_mult_offense = None
    if team_split_rpg is not None and team_season_rpg is not None and team_season_rpg > 0:
        raw_ratio = team_split_rpg / team_season_rpg
        # Blend toward 1.0 by (1 - split_weight). e.g. split_weight=0.5 means
        # halfway between season baseline (1.0) and full split ratio.
        sw = w['offense_split_weight']
        split_mult_offense = 1.0 + (raw_ratio - 1.0) * sw

    # === Compute composites ===
    wrc_blend = _blend_wrc_plus(wrc_plus, wrc_vs_hand, wrc_l14, w)
    offense_mult = _offense_quality_multiplier(wrc_blend, barrel, xwoba, w)

    # Defense reductions (apply to opposing offense's run scoring against them)
    defense_mult = 1.0
    if opp_oaa is not None:
        defense_mult *= 1.0 - (opp_oaa - w['oaa_baseline']) * w['oaa_per_unit']
    if opp_framing is not None:
        defense_mult *= 1.0 - (opp_framing - w['framing_baseline']) * w['framing_per_unit']
    offense_mult *= defense_mult

    # Home/away split: prefer team-specific split if available, else generic
    if split_mult_offense is not None:
        offense_mult *= split_mult_offense
    elif team_side == 'home':
        offense_mult *= w['offense_home_advantage_default']

    # Mastery + split multipliers (on the opposing pitcher's allowed runs)
    mastery_mult = _mastery_multiplier(opp_mastery_era, opp_mastery_ip, w)
    split_mult = _starter_split_multiplier(opp_split_delta, w)

    # === Inning bucket projections ===
    # 1-3: starter is fresh, use bucket era if available, else composite with 1st-inn weighted
    rate_1_3 = opp_bucket_1_3 or _starter_composite_rate(opp_xera, opp_l3_era, opp_first_inn, w, for_first_inning=True)
    runs_1_3 = _bucket_runs(rate_1_3, bucket_offense_1_3, offense_mult, mastery_mult, split_mult, w['bucket_1_3_weight'], w)

    # 4-6: starter fatiguing, blend with bullpen per share weights
    starter_4_6 = opp_bucket_4_6 or _starter_composite_rate(opp_xera, opp_l3_era, opp_first_inn, w)
    if starter_4_6 is not None and opp_bullpen_era is not None:
        rate_4_6 = (w['bucket_4_6_starter_share'] * starter_4_6 +
                    w['bucket_4_6_bullpen_share'] * opp_bullpen_era)
    else:
        rate_4_6 = starter_4_6 or opp_bullpen_era
    runs_4_6 = _bucket_runs(rate_4_6, bucket_offense_4_6, offense_mult, mastery_mult, split_mult, w['bucket_4_6_weight'], w)

    # 7-9: bullpen only, gas penalty applies
    gas_penalty = _bullpen_gas_penalty(opp_bullpen_gas, w)
    bullpen_late = (opp_bullpen_7_9 or opp_bullpen_era)
    if bullpen_late is not None:
        bullpen_late_adjusted = bullpen_late * (1.0 + gas_penalty)
    else:
        bullpen_late_adjusted = None
    # No mastery/split on bullpen — those are starter-specific
    runs_7_9 = _bucket_runs(bullpen_late_adjusted, bucket_offense_7_9, offense_mult, 1.0, 1.0, w['bucket_7_9_weight'], w)

    # === Game-level multipliers (park + weather) ===
    park_mult = 1.0 + (park - w['park_baseline']) / 100 * w['park_sensitivity']
    temp_mult = 1.0 + (temp - w['temp_baseline']) * w['temp_per_degree']

    wind_mult = 1.0
    wind_out = wind_speed >= w['wind_speed_threshold_out'] and any(d in wind_dir for d in ('S', 'SW', 'SE', 'OUT'))
    wind_in = wind_speed >= w['wind_speed_threshold_in'] and any(d in wind_dir for d in ('N', 'NW', 'NE', 'IN'))
    if wind_out:
        wind_mult = w['wind_out_multiplier']
    elif wind_in:
        wind_mult = w['wind_in_multiplier']

    base_runs = runs_1_3 + runs_4_6 + runs_7_9
    final_runs = base_runs * park_mult * temp_mult * wind_mult

    # Sanity clamp
    final_runs = max(w['min_team_runs'], min(w['max_team_runs'], final_runs))

    breakdown = {
        'wrc_blend': round(wrc_blend, 1) if wrc_blend is not None else None,
        'offense_mult': round(offense_mult, 3),
        'team_split_rpg': team_split_rpg,
        'team_season_rpg': team_season_rpg,
        'offense_split_mult': round(split_mult_offense, 3) if split_mult_offense is not None else None,
        'mastery_mult': round(mastery_mult, 3),
        'starter_split_mult': round(split_mult, 3),
        'opp_starter_rate_1_3': round(rate_1_3, 2) if rate_1_3 else None,
        'opp_starter_rate_4_6': round(rate_4_6, 2) if rate_4_6 else None,
        'opp_bullpen_rate_7_9': round(bullpen_late_adjusted, 2) if bullpen_late_adjusted else None,
        'bullpen_gas_penalty': round(gas_penalty, 3),
        'runs_1_3': round(runs_1_3, 2),
        'runs_4_6': round(runs_4_6, 2),
        'runs_7_9': round(runs_7_9, 2),
        'base_runs': round(base_runs, 2),
        'park_mult': round(park_mult, 3),
        'temp_mult': round(temp_mult, 3),
        'wind_mult': round(wind_mult, 3),
        'final_runs': round(final_runs, 2),
    }

    return final_runs, breakdown


def compute_jerry_projection(ctx: Dict[str, Any], weights: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run the Jerry Model on an enriched game context.

    The ctx must include the standard mlb_game_context fields PLUS the
    inning bucket data (innings_1_3_era / 4_6 / 7_9 for both pitchers,
    innings_1_3_runs_per_game / 4_6 / 7_9 for both teams, and
    pitching_7_9_era for both bullpens). See enrich_ctx_for_jerry() to
    merge those in from mlb_pitcher_stats / mlb_team_offense / mlb_bullpen_stats.

    Returns:
        {
          jerry_away_runs, jerry_home_runs, jerry_total, jerry_spread,
          components: {away: {...}, home: {...}},
          weights_version, missing_inputs: [...]
        }
    """
    w = weights or JERRY_WEIGHTS

    # Track which inputs are missing for audit purposes
    required_per_side = ('_sp_xera', '_wrc_plus', '_bullpen_era')
    missing = []
    for side in ('away', 'home'):
        for key in required_per_side:
            if ctx.get(f'{side}{key}') is None:
                missing.append(f'{side}{key}')

    away_runs, away_breakdown = _project_team_runs(ctx, 'away', w)
    home_runs, home_breakdown = _project_team_runs(ctx, 'home', w)

    return {
        'jerry_away_runs': round(away_runs, 2),
        'jerry_home_runs': round(home_runs, 2),
        'jerry_total': round(away_runs + home_runs, 2),
        'jerry_spread': round(home_runs - away_runs, 2),
        'components': {
            'away': away_breakdown,
            'home': home_breakdown,
        },
        'weights_version': w.get('_version', 'unknown'),
        'missing_inputs': missing,
    }


# =============================================================================
# Enrichment — pulls inning bucket data + bullpen 7-9 ERA into ctx
# =============================================================================
def enrich_ctx_for_jerry(ctx, pitcher_stats_by_name=None, team_offense_by_team=None, bullpen_stats_by_team=None):
    """Merge inning-bucket inputs into a game context dict. Pass in pre-loaded
    lookup dicts when scoring many games (cache cost amortizes). Returns a
    NEW dict — does not mutate input.

    pitcher_stats_by_name: {player_name: {innings_1_3_era, innings_4_6_era, innings_7_9_era, ...}}
    team_offense_by_team:  {team_name: {innings_1_3_runs_per_game, ...}}
    bullpen_stats_by_team: {team_name: {pitching_1_3_era, pitching_4_6_era, pitching_7_9_era}}
    """
    enriched = dict(ctx)
    for side in ('home', 'away'):
        pitcher = ctx.get(f'{side}_pitcher')
        if pitcher and pitcher_stats_by_name and pitcher in pitcher_stats_by_name:
            ps = pitcher_stats_by_name[pitcher]
            enriched[f'{side}_innings_1_3_era'] = ps.get('innings_1_3_era')
            enriched[f'{side}_innings_4_6_era'] = ps.get('innings_4_6_era')
            enriched[f'{side}_innings_7_9_era'] = ps.get('innings_7_9_era')
        team = ctx.get(f'{side}_team')
        if team and team_offense_by_team and team in team_offense_by_team:
            to = team_offense_by_team[team]
            enriched[f'{side}_innings_1_3_runs_per_game'] = to.get('innings_1_3_runs_per_game')
            enriched[f'{side}_innings_4_6_runs_per_game'] = to.get('innings_4_6_runs_per_game')
            enriched[f'{side}_innings_7_9_runs_per_game'] = to.get('innings_7_9_runs_per_game')
            # Per-team home/away R/G splits (5/30 enhancement)
            enriched[f'{side}_runs_per_game_season'] = to.get('runs_per_game')
            enriched[f'{side}_runs_per_game_home'] = to.get('runs_per_game_home')
            enriched[f'{side}_runs_per_game_away'] = to.get('runs_per_game_away')
            # Also surface OPS splits in case we want to use them later
            enriched[f'{side}_ops_home'] = to.get('ops_home')
            enriched[f'{side}_ops_away'] = to.get('ops_away')
        if team and bullpen_stats_by_team and team in bullpen_stats_by_team:
            bs = bullpen_stats_by_team[team]
            enriched[f'{side}_pitching_7_9_era'] = bs.get('pitching_7_9_era')
    return enriched
