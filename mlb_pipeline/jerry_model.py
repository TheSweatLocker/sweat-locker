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
    # 6/2 (Phase 1 recalibration): switched from fixed-alpha to Bayesian shrinkage
    # in _blend_wrc_plus. The fixed alpha=0.50 was over-weighting recency on hot
    # streaks — 6/1 + 6/2 audit showed Jerry's high-conv calls bombing because
    # multipliers stacked too aggressively when teams were on form streaks that
    # mean-revert. Bayesian shrinkage automatically dampens recency when recent
    # sample is small relative to the prior, and weights more toward recency
    # when sample is large. Tunable via wrc_*_prior_pa keys below.
    #
    # NOTE: offense_recency_alpha + offense_l7_alpha kept as fallbacks for any
    # external caller still using the old API. _blend_wrc_plus ignores them now.
    'offense_recency_alpha': 0.50,
    'offense_l7_alpha': 0.20,
    'starter_l3_alpha': 0.30,        # 30% L3 ERA, 70% season xERA

    # --- Bayesian shrinkage priors (NEW 6/2) ---
    # projected_metric = (season × prior_pa + recent × recent_pa) / (prior_pa + recent_pa)
    # Larger prior = more shrinkage toward season baseline (less trust in recent).
    # Per-metric priors because BABIP/OPS regress faster than skill metrics.
    #
    # Effective recency weights at these defaults:
    #   wRC+ L14 (recent_pa ~70) with prior 250 → 22% recent vs 78% season
    #     (vs old fixed 50/50 — much more conservative on hot streaks)
    #
    # Tune downward (smaller prior) when audit shows we're too slow to catch
    # real form changes; tune upward when high-conv calls keep losing.
    'wrc_l14_prior_pa': 250,         # Bayesian prior strength for L14 wRC+ shrinkage
    'wrc_l14_recent_pa': 70,         # implied sample size from 14-day window (~14 games × 5 PA)
    'wrc_vs_hand_prior_pa': 150,     # vs-hand season slice — smaller prior since often smaller sample
    'wrc_vs_hand_recent_pa_default': 200,  # rough season-level recent sample for vs-hand

    # Mean reversion gate (NEW 6/2) — last line of defense against runaway
    # recency multipliers. If |recent - season| / season > threshold, apply
    # dampener. Catches 1.5+σ outliers that Bayesian alone doesn't fully tame.
    'mean_reversion_threshold': 0.18,  # 18% deviation from season triggers gate
    'mean_reversion_dampener': 0.70,   # dampens the recency contribution by this factor

    # --- Pitcher rest + last-outing fatigue (NEW 6/2) ---
    # Data already present in mlb_game_context as {side}_sp_days_rest and
    # {side}_last_pitch_count — but Jerry wasn't reading them. Real signal:
    # pitchers coming off heavy outings on short rest underperform their
    # xERA, and pitchers off light outings on long rest outperform.
    #
    # Multiplier applied to opponent's run rate (higher mult = opponent
    # scores more = pitcher is fatigued / undermined). Capped tight because
    # this is a marginal signal, not a primary one.
    'pitcher_normal_rest_days': 5,         # baseline: 5 days rest is "normal" between starts
    'pitcher_normal_pitch_count': 90,      # baseline: 90 pitches is "normal" workload
    'pitcher_fatigued_pitch_count': 105,   # >this on prior outing = heavy workload
    'pitcher_fresh_pitch_count': 75,       # <this on prior outing = light workload
    'pitcher_short_rest_days': 4,          # ≤this = short rest
    'pitcher_long_rest_days': 6,           # ≥this = extra rest
    'pitcher_fatigue_multiplier_high': 1.05,  # fatigued (heavy + short) → opp scores +5%
    'pitcher_fatigue_multiplier_mild': 1.03,  # mild fatigue (heavy OR short alone) → +3%
    'pitcher_freshness_multiplier_high': 0.97, # fresh (light + long) → opp scores -3%
    'pitcher_freshness_multiplier_mild': 0.985, # mild freshness (light OR long alone) → -1.5%

    # --- Hot/cold bats drift (NEW 5/30) ---
    # Uses offense_drift = L10 R/G - season R/G (precomputed in game_context).
    # Each +1.0 R/G above season → +sensitivity% runs.
    # Capped to ±30% to prevent runaway projections on small samples.
    # Same signal the props pipeline uses for team_heat / team_cold.
    'offense_drift_sensitivity': 0.15,
    'offense_drift_cap_high': 1.30,
    'offense_drift_cap_low': 0.70,

    # --- L10 win% team momentum (NEW 5/30, per user direction) ---
    # L10 W-L is a different signal from drift (which is offensive-only):
    # captures pitching/bullpen momentum + clutch performance + manager
    # decisions. A team going 7-3 winning low-scoring games is different
    # from a team going 3-7 with similar R/G. Symmetric around .500:
    #   momentum_mult = 1.0 + (l10_win_pct - 0.5) * sensitivity
    # At sensitivity 0.15: 80% → +4.5%, 20% → -4.5%, 100% → +7.5%
    'team_l10_momentum_sensitivity': 0.15,
    'team_l10_min_games': 7,  # need at least 7 graded games to apply

    # --- Starter component ---
    'starter_xera_weight': 0.55,     # base weight on xERA
    'starter_l3_weight': 0.30,       # L3 form weight
    'starter_first_inn_weight': 0.15, # 1st-inn ERA (used specifically for inning 1-3 bucket)
    'starter_ip_estimate': 5.5,      # innings the starter typically throws
    'starter_split_sensitivity': 0.10, # each +0.5 ERA worse in this split = +5% runs allowed

    # --- Mastery (vs current opp) adjustment ---
    'mastery_ip_gate': 15,           # below this IP, career mastery doesn't fire
    'mastery_strong_era': 2.50,      # mastery threshold
    'mastery_weak_era': 6.00,        # anti-mastery threshold
    'mastery_strong_multiplier': 0.85,  # opp runs × 0.85 when strong mastery
    'mastery_weak_multiplier': 1.18,    # opp runs × 1.18 when anti-mastery

    # Recent mastery (L3 starts vs opp) — added 5/30 per user direction.
    # When recent ERA differs from career by ≥2.0 AND recent has ≥10 IP,
    # blend recent into the multiplier so the model captures current form.
    # Pure recent (100%) when delta is large; pure career when delta small.
    'mastery_recent_ip_gate': 10,
    'mastery_recent_delta_threshold': 2.0,  # career - recent ERA difference to flip
    'mastery_recent_blend_at_threshold': 0.65,  # 65% recent / 35% career when delta = threshold
    'mastery_recent_max_blend': 0.85,           # max 85% recent weight

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
    # 5/30: temp sensitivity reduced from 0.0030 → 0.0020 per user note that
    # weather is over-weighted in summer (most games 70-85°F, +/-10° = ±2% runs).
    # Wind unchanged — still material when blowing strongly out/in.
    'temp_baseline': 70,
    'temp_per_degree': 0.0020,
    'wind_speed_threshold_out': 8,
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


def _bayesian_blend(season_value, recent_value, prior_pa, recent_pa, w):
    """Sample-weighted Bayesian shrinkage of a recent observation toward season.

    Formula:
        blended = (season × prior_pa + recent × recent_pa) / (prior_pa + recent_pa)

    Larger prior_pa = more shrinkage toward season (less trust in recent).
    Smaller recent_pa = less weight on the recent value (auto-dampens noisy samples).

    Includes mean-reversion gate (6/2 addition) — when the recent value deviates
    more than `mean_reversion_threshold` from season, dampen the recent contribution
    by `mean_reversion_dampener`. Catches the runaway-multiplier failure mode that
    Bayesian alone doesn't fully tame on 1.5+σ outliers (DET 8-0 over TB despite
    "TB trending down" signal — Jerry trusted form too much).
    """
    if season_value is None:
        return recent_value
    if recent_value is None or recent_pa <= 0:
        return season_value

    # Mean-reversion gate
    effective_recent_pa = recent_pa
    if season_value != 0:
        deviation = abs(recent_value - season_value) / abs(season_value)
        if deviation > w['mean_reversion_threshold']:
            effective_recent_pa = recent_pa * w['mean_reversion_dampener']

    denom = prior_pa + effective_recent_pa
    if denom <= 0:
        return season_value
    return (season_value * prior_pa + recent_value * effective_recent_pa) / denom


def _blend_wrc_plus(season, vs_hand, l14, w):
    """Blend wRC+ inputs into a recency-and-platoon-adjusted number.

    Uses Bayesian shrinkage (6/2 redesign) — recent samples get weight based on
    their effective sample size relative to a configurable prior, instead of
    the old fixed-alpha (alpha=0.50) blend that over-weighted hot streaks.

    Two stages:
      1. Platoon blend: shrink vs_hand wRC+ toward season using a vs-hand prior
      2. Recency blend: shrink L14 wRC+ toward the platoon-adjusted season

    A team with L14 wRC+ 145 vs season 100 (45-point hot streak) used to give
    blended = 122 (50/50). Now with prior 250 / recent 70:
       (100 × 250 + 145 × 70) / 320 = 109.8 — much more conservative.
    The mean-reversion gate fires too (45 / 100 = 0.45 > 0.18 threshold),
    further dampening recent weight by 0.70: blended ~106. Catches the runaway.
    """
    if season is None:
        return None

    # Stage 1: vs-hand platoon shrinkage
    platoon_adjusted = season
    if vs_hand is not None:
        # vs-hand season slice typically has a partial-season sample; we
        # approximate it at wrc_vs_hand_recent_pa_default (~200 PA equivalent).
        platoon_adjusted = _bayesian_blend(
            season, vs_hand,
            prior_pa=w['wrc_vs_hand_prior_pa'],
            recent_pa=w['wrc_vs_hand_recent_pa_default'],
            w=w,
        )

    # Stage 2: L14 recency shrinkage on top of platoon-adjusted season
    if l14 is not None:
        return _bayesian_blend(
            platoon_adjusted, l14,
            prior_pa=w['wrc_l14_prior_pa'],
            recent_pa=w['wrc_l14_recent_pa'],
            w=w,
        )
    return platoon_adjusted


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


def _mastery_multiplier(vs_team_era, vs_team_ip, w, recent_era=None, recent_ip=None):
    """Multiplier applied to opposing-runs based on mastery / anti-mastery.

    Default behavior unchanged: only fires when career IP >= gate; linearly
    interpolates between strong (≤2.50 ERA → 0.85) and weak (≥6.00 ERA → 1.18).

    5/30: when recent_era + recent_ip are provided AND recent has ≥10 IP AND
    recent ERA differs from career by ≥2.0, blend recent into the multiplier
    so current form gets weight. The blend is graduated — small disagreement
    keeps career dominant, large disagreement (≥4.0 ERA delta) flips to
    mostly recent.
    """
    if vs_team_era is None or vs_team_ip is None or vs_team_ip < w['mastery_ip_gate']:
        # Career signal missing — fall back to recent alone if available
        if recent_era is not None and recent_ip is not None and recent_ip >= w['mastery_recent_ip_gate']:
            return _era_to_multiplier(recent_era, w)
        return 1.0

    career_mult = _era_to_multiplier(vs_team_era, w)
    if recent_era is None or recent_ip is None or recent_ip < w['mastery_recent_ip_gate']:
        return career_mult

    # Both signals present — graduated blend based on disagreement size
    delta = abs(vs_team_era - recent_era)
    threshold = w['mastery_recent_delta_threshold']
    if delta < threshold:
        return career_mult  # career still dominant when recent agrees
    # Linear scale: at threshold → blend_at_threshold; at 2x threshold → max_blend
    recent_weight = w['mastery_recent_blend_at_threshold'] + \
                    (delta - threshold) / threshold * \
                    (w['mastery_recent_max_blend'] - w['mastery_recent_blend_at_threshold'])
    recent_weight = max(0.0, min(w['mastery_recent_max_blend'], recent_weight))
    recent_mult = _era_to_multiplier(recent_era, w)
    return career_mult * (1 - recent_weight) + recent_mult * recent_weight


def _era_to_multiplier(era, w):
    """ERA → mastery multiplier on a linear band between strong and weak thresholds."""
    if era <= w['mastery_strong_era']:
        return w['mastery_strong_multiplier']
    if era >= w['mastery_weak_era']:
        return w['mastery_weak_multiplier']
    span = w['mastery_weak_era'] - w['mastery_strong_era']
    pos = (era - w['mastery_strong_era']) / span
    mult_span = w['mastery_weak_multiplier'] - w['mastery_strong_multiplier']
    return w['mastery_strong_multiplier'] + pos * mult_span


def _pitcher_form_multiplier(days_rest, last_pitch_count, w):
    """Fatigue / freshness multiplier from rest + last-outing pitch count.

    Applied to opponent's run rate (higher multiplier = opp scores more
    = pitcher is undermined). Reads {side}_sp_days_rest and
    {side}_last_pitch_count from game_context — data we've had all along
    but Jerry wasn't using.

    Decision matrix (heavy = pitch count > fatigued_pitch_count,
                     light = pitch count < fresh_pitch_count,
                     short = days_rest ≤ short_rest_days,
                     long  = days_rest ≥ long_rest_days):

      heavy + short → high fatigue (worst case)
      heavy + long  → mild fatigue (extra rest helps but not enough)
      light + short → mild freshness
      light + long  → high freshness (best case — sharp pitcher)
      one signal alone → mild
      neither → neutral 1.0
    """
    if days_rest is None and last_pitch_count is None:
        return 1.0

    is_fatigued_workload = (last_pitch_count is not None
                            and last_pitch_count > w['pitcher_fatigued_pitch_count'])
    is_fresh_workload = (last_pitch_count is not None
                         and last_pitch_count < w['pitcher_fresh_pitch_count'])
    is_short_rest = (days_rest is not None
                     and days_rest <= w['pitcher_short_rest_days'])
    is_long_rest = (days_rest is not None
                    and days_rest >= w['pitcher_long_rest_days'])

    # Worst-case fatigue: heavy workload + short rest
    if is_fatigued_workload and is_short_rest:
        return w['pitcher_fatigue_multiplier_high']
    # Best-case freshness: light workload + long rest
    if is_fresh_workload and is_long_rest:
        return w['pitcher_freshness_multiplier_high']
    # Single-signal fatigue (heavy workload OR short rest alone)
    if is_fatigued_workload or is_short_rest:
        return w['pitcher_fatigue_multiplier_mild']
    # Single-signal freshness
    if is_fresh_workload or is_long_rest:
        return w['pitcher_freshness_multiplier_mild']
    return 1.0


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
    # 2026-06-03: the legacy {side}_pitcher_split_delta column is always NULL
    # — get_pitcher_splits() in game_context.py never returns a 'split_delta'
    # key (only home_era/away_era/IP). Compute the delta directly here from
    # the venue ERA vs xERA. opp_side is 'home' or 'away' and they're pitching
    # AT that venue tonight, so the relevant split is e.g. away_pitcher_away_era.
    # IP gate is already applied in the helper (None when <15 IP per side).
    if opp_split_delta is None:
        venue_era = _f(ctx.get(f'{opp_side}_pitcher_{opp_side}_era'))
        baseline = _f(ctx.get(f'{opp_side}_sp_xera'))
        if venue_era is not None and baseline is not None:
            opp_split_delta = round(venue_era - baseline, 2)
    opp_mastery_era = _f(ctx.get(f'{opp_side}_pitcher_vs_team_era'))
    opp_mastery_ip = _f(ctx.get(f'{opp_side}_pitcher_vs_team_ip'))
    # Recent mastery (L3 starts vs opp) — added 5/30
    opp_mastery_recent_era = _f(ctx.get(f'{opp_side}_pitcher_vs_team_recent_era'))
    opp_mastery_recent_ip = _f(ctx.get(f'{opp_side}_pitcher_vs_team_recent_ip'))

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

    # === Hot/cold bats drift (NEW 5/30) ===
    # offense_drift = L10 R/G - season R/G. Positive = hot bats, negative = slumping.
    # User flagged this as THE key baseball-modeling signal — capturing transitions
    # from hot to cold (and vice versa) is what separates good models from noise.
    drift = _f(ctx.get(f'{team_side}_offense_drift'))
    drift_mult = 1.0
    if drift is not None:
        drift_mult = 1.0 + drift * w['offense_drift_sensitivity']
        drift_mult = max(w['offense_drift_cap_low'], min(w['offense_drift_cap_high'], drift_mult))

    # === L10 team momentum (NEW 5/30) ===
    # L10 W-L is a different signal from drift — captures pitching, clutch,
    # team-overall momentum. Read from ctx as wins/losses; computed by
    # game_context.py from mlb_game_results.
    l10_wins = _f(ctx.get(f'{team_side}_l10_wins'))
    l10_losses = _f(ctx.get(f'{team_side}_l10_losses'))
    momentum_mult = 1.0
    if l10_wins is not None and l10_losses is not None:
        l10_n = l10_wins + l10_losses
        if l10_n >= w['team_l10_min_games']:
            l10_win_pct = l10_wins / l10_n
            momentum_mult = 1.0 + (l10_win_pct - 0.5) * w['team_l10_momentum_sensitivity']

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

    # Apply hot/cold drift multiplier
    offense_mult *= drift_mult

    # Apply L10 team momentum multiplier
    offense_mult *= momentum_mult

    # Mastery + split multipliers (on the opposing pitcher's allowed runs).
    # 5/30: now factors in recent (L3-start) mastery alongside career.
    mastery_mult = _mastery_multiplier(opp_mastery_era, opp_mastery_ip, w,
                                       recent_era=opp_mastery_recent_era,
                                       recent_ip=opp_mastery_recent_ip)
    split_mult = _starter_split_multiplier(opp_split_delta, w)

    # 6/2 (Phase 1): pitcher form (fatigue/freshness) based on rest + last
    # outing pitch count. Reads {opp_side}_sp_days_rest +
    # {opp_side}_last_pitch_count from context — both already populated by
    # game_context.py, never consumed by Jerry until now.
    # Applied as a multiplier on opp pitcher → higher = opp scores more
    # against us = our offense benefits (which is what we want when the
    # opponent's pitcher is tired). Same multiplier flows through both the
    # 1-3 and 4-6 buckets where the starter is the primary signal.
    opp_pitcher_days_rest = _f(ctx.get(f'{opp_side}_sp_days_rest'))
    opp_pitcher_last_pitches = _f(ctx.get(f'{opp_side}_last_pitch_count'))
    pitcher_form_mult = _pitcher_form_multiplier(
        opp_pitcher_days_rest, opp_pitcher_last_pitches, w,
    )

    # === Inning bucket projections ===
    # Pitcher form multiplier (rest + last-outing pitches) folds into the
    # starter-period split_mult so it applies multiplicatively alongside
    # mastery + home/away splits without introducing a new bucket param.
    starter_period_split_mult = split_mult * pitcher_form_mult

    # 1-3: starter is fresh, use bucket era if available, else composite with 1st-inn weighted
    rate_1_3 = opp_bucket_1_3 or _starter_composite_rate(opp_xera, opp_l3_era, opp_first_inn, w, for_first_inning=True)
    runs_1_3 = _bucket_runs(rate_1_3, bucket_offense_1_3, offense_mult, mastery_mult, starter_period_split_mult, w['bucket_1_3_weight'], w)

    # 4-6: starter fatiguing, blend with bullpen per share weights
    starter_4_6 = opp_bucket_4_6 or _starter_composite_rate(opp_xera, opp_l3_era, opp_first_inn, w)
    if starter_4_6 is not None and opp_bullpen_era is not None:
        rate_4_6 = (w['bucket_4_6_starter_share'] * starter_4_6 +
                    w['bucket_4_6_bullpen_share'] * opp_bullpen_era)
    else:
        rate_4_6 = starter_4_6 or opp_bullpen_era
    runs_4_6 = _bucket_runs(rate_4_6, bucket_offense_4_6, offense_mult, mastery_mult, starter_period_split_mult, w['bucket_4_6_weight'], w)

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
        'offense_drift': drift,
        'drift_mult': round(drift_mult, 3),
        'l10_wins': l10_wins,
        'l10_losses': l10_losses,
        'momentum_mult': round(momentum_mult, 3),
        'mastery_mult': round(mastery_mult, 3),
        'mastery_recent_era': opp_mastery_recent_era,
        'mastery_recent_ip': opp_mastery_recent_ip,
        'starter_split_mult': round(split_mult, 3),
        # Phase 1 (6/2): pitcher form audit trail
        'opp_pitcher_days_rest': opp_pitcher_days_rest,
        'opp_pitcher_last_pitches': opp_pitcher_last_pitches,
        'pitcher_form_mult': round(pitcher_form_mult, 3),
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


# =============================================================================
# PER-BATTER HR CONTRIBUTION (added 2026-06-01)
#
# User direction: "Like #3" — Jerry per-batter integration. Phase 1 ships
# a focused HR allocator that reuses Jerry's situational machinery (park,
# wind, temp, mastery-style multipliers) and applies them to per-batter
# HR/PA rates. Lives in shadow mode alongside the legacy HR Watch score
# for 7-14 days; if audit shows lift, blended into final ranking.
#
# Design philosophy mirrors team Jerry: transparent linear formula, all
# tunables in a config dict (JERRY_HR_WEIGHTS below). No black box.
# =============================================================================
JERRY_HR_WEIGHTS = {
    # Lineup-spot PA allocation. League-average PAs per game by batting
    # order position (1st = most, 9th = least). Source: 5-year MLB
    # averages — top-of-order gets ~1 extra PA over bottom-of-order
    # across a 162-game sample.
    'pa_by_lineup_spot': {
        1: 4.50, 2: 4.40, 3: 4.30, 4: 4.20, 5: 4.10,
        6: 3.90, 7: 3.70, 8: 3.50, 9: 3.30,
    },
    'pa_fallback': 4.00,  # unknown lineup spot

    # Bayesian regression prior matches build_hr_watch.py (PRIOR_PA=400)
    # so the per-batter rate is regressed consistently across surfaces.
    'prior_hr_rate': 0.03,
    'prior_pa': 400,

    # Park HR factor → multiplier. Coors 123 → 1.23x, Petco 88 → 0.88x.
    # Directly proportional; cap at 1.30 / 0.85 to prevent extreme parks
    # from dominating.
    'park_mult_min': 0.85,
    'park_mult_max': 1.30,

    # Temperature: 80°F+ → 1.10x, 70°F+ → 1.05x, <50°F → 0.90x.
    'temp_hot_threshold': 80,
    'temp_hot_mult': 1.10,
    'temp_warm_threshold': 70,
    'temp_warm_mult': 1.05,
    'temp_cold_threshold': 50,
    'temp_cold_mult': 0.90,

    # Wind out: blowing out (S/SW/SE) at >10mph → 1.12x.
    'wind_out_mult': 1.12,
    'wind_in_mult': 0.92,

    # Pitcher flyball tilt: fb_pct >= .40 → 1.15x (lots of fly balls
    # become HRs in HR-friendly conditions). fb_pct <= .30 → 0.88x
    # (groundball pitchers suppress HR).
    'pitcher_fb_high_threshold': 0.40,
    'pitcher_fb_high_mult': 1.15,
    'pitcher_fb_low_threshold': 0.30,
    'pitcher_fb_low_mult': 0.88,

    # Pitcher xERA: bad pitchers give up more HRs.
    'pitcher_xera_bad_threshold': 4.50,
    'pitcher_xera_bad_mult': 1.15,
    'pitcher_xera_good_threshold': 3.20,
    'pitcher_xera_good_mult': 0.90,

    # Platoon: opposite hand → 1.07x, same hand → 0.94x, switch → 1.07x.
    'platoon_opposite_mult': 1.07,
    'platoon_same_mult': 0.94,

    # Statcast plus: barrel% >= 11 → 1.10x, hard_hit >= 45 → 1.05x.
    'statcast_barrel_threshold': 0.11,  # or 11 if stored as percent — normalize on read
    'statcast_barrel_mult': 1.10,
    'statcast_hardhit_threshold': 0.45,
    'statcast_hardhit_mult': 1.05,
}


def _hr_lineup_pa(spot):
    """Allocated PAs for this batting order spot. Falls back to 4.0 PA when
    the spot is unknown (lineup_state=None / hitter not in our top-5 walk)."""
    w = JERRY_HR_WEIGHTS
    if spot is None:
        return w['pa_fallback']
    try:
        return w['pa_by_lineup_spot'].get(int(spot), w['pa_fallback'])
    except (TypeError, ValueError):
        return w['pa_fallback']


def _hr_park_mult(park_hr_factor):
    """Linear park HR multiplier, clamped to [pmin, pmax]."""
    w = JERRY_HR_WEIGHTS
    try:
        raw = float(park_hr_factor) / 100.0
        return max(w['park_mult_min'], min(w['park_mult_max'], raw))
    except (TypeError, ValueError):
        return 1.0


def _hr_temp_mult(temp_f):
    w = JERRY_HR_WEIGHTS
    if temp_f is None:
        return 1.0
    try:
        t = float(temp_f)
    except (TypeError, ValueError):
        return 1.0
    if t >= w['temp_hot_threshold']:
        return w['temp_hot_mult']
    if t >= w['temp_warm_threshold']:
        return w['temp_warm_mult']
    if t < w['temp_cold_threshold']:
        return w['temp_cold_mult']
    return 1.0


def _hr_wind_mult(wind_speed, wind_dir):
    """Out-blowing wind = HR boost; in-blowing = suppress."""
    w = JERRY_HR_WEIGHTS
    if wind_speed is None or wind_dir is None:
        return 1.0
    try:
        ws = float(wind_speed)
    except (TypeError, ValueError):
        return 1.0
    if ws <= 10:
        return 1.0
    d = (wind_dir or '').upper()
    out_dirs = {'S', 'SW', 'SE', 'OUT'}
    in_dirs = {'N', 'NW', 'NE', 'IN'}
    if any(o in d for o in out_dirs):
        return w['wind_out_mult']
    if any(i in d for i in in_dirs):
        return w['wind_in_mult']
    return 1.0


def _hr_pitcher_fb_mult(fb_pct):
    w = JERRY_HR_WEIGHTS
    if fb_pct is None:
        return 1.0
    try:
        fb = float(fb_pct)
    except (TypeError, ValueError):
        return 1.0
    if fb > 1.0:  # stored as percent (35.0 not 0.35) — normalize
        fb = fb / 100.0
    if fb >= w['pitcher_fb_high_threshold']:
        return w['pitcher_fb_high_mult']
    if fb <= w['pitcher_fb_low_threshold']:
        return w['pitcher_fb_low_mult']
    return 1.0


def _hr_pitcher_xera_mult(xera):
    w = JERRY_HR_WEIGHTS
    if xera is None:
        return 1.0
    try:
        x = float(xera)
    except (TypeError, ValueError):
        return 1.0
    if x >= w['pitcher_xera_bad_threshold']:
        return w['pitcher_xera_bad_mult']
    if x <= w['pitcher_xera_good_threshold']:
        return w['pitcher_xera_good_mult']
    return 1.0


def _hr_platoon_mult(bat_side, pitcher_throws):
    w = JERRY_HR_WEIGHTS
    if not bat_side or not pitcher_throws:
        return 1.0
    b = (bat_side or '').upper()
    p = (pitcher_throws or '').upper()
    if b == 'S':  # switch — always plays opposite
        return w['platoon_opposite_mult']
    if b != p:
        return w['platoon_opposite_mult']
    return w['platoon_same_mult']


def _hr_statcast_mult(barrel_pct, hard_hit_pct):
    """Statcast plus multipliers. Normalize percent vs fraction storage."""
    w = JERRY_HR_WEIGHTS
    mult = 1.0
    if barrel_pct is not None:
        try:
            b = float(barrel_pct)
            if b > 1.0:
                b = b / 100.0  # normalize percent
            if b >= w['statcast_barrel_threshold']:
                mult *= w['statcast_barrel_mult']
        except (TypeError, ValueError):
            pass
    if hard_hit_pct is not None:
        try:
            h = float(hard_hit_pct)
            if h > 1.0:
                h = h / 100.0
            if h >= w['statcast_hardhit_threshold']:
                mult *= w['statcast_hardhit_mult']
        except (TypeError, ValueError):
            pass
    return mult


def compute_batter_hr_contribution(
    *,
    season_hr,
    season_pa,
    bat_side=None,
    lineup_spot=None,
    park_hr_factor=100,
    temperature=None,
    wind_speed=None,
    wind_direction=None,
    opp_pitcher_xera=None,
    opp_pitcher_fb_pct=None,
    opp_pitcher_throws=None,
    barrel_pct=None,
    hard_hit_pct=None,
) -> Dict[str, Any]:
    """Jerry's per-batter expected HR contribution for tonight's game.

    Formula (transparent, all weights in JERRY_HR_WEIGHTS above):

        regressed_rate = (HR + 0.03 * 400) / (PA + 400)
        expected_hr    = regressed_rate * allocated_pa * \\
                         park_mult * temp_mult * wind_mult * \\
                         fb_mult * xera_mult * platoon_mult * statcast_mult

    Returns dict with the contribution number AND every component
    multiplier so the audit + app can render a transparent breakdown
    instead of an opaque "0.31".

    Edge cases:
      - season_pa < 40: returns None (insufficient sample, same as score gate)
      - missing inputs: each multiplier defaults to 1.0 (neutral)
    """
    w = JERRY_HR_WEIGHTS
    if season_pa is None or season_pa < 40 or season_hr is None:
        return {'jerry_hr_contribution': None,
                'jerry_signals': {'reason': 'insufficient_sample'},
                'jerry_allocated_pa': None}

    regressed_rate = (season_hr + w['prior_hr_rate'] * w['prior_pa']) / (season_pa + w['prior_pa'])
    allocated_pa = _hr_lineup_pa(lineup_spot)

    park_m = _hr_park_mult(park_hr_factor)
    temp_m = _hr_temp_mult(temperature)
    wind_m = _hr_wind_mult(wind_speed, wind_direction)
    fb_m = _hr_pitcher_fb_mult(opp_pitcher_fb_pct)
    xera_m = _hr_pitcher_xera_mult(opp_pitcher_xera)
    platoon_m = _hr_platoon_mult(bat_side, opp_pitcher_throws)
    statcast_m = _hr_statcast_mult(barrel_pct, hard_hit_pct)

    raw_expected = regressed_rate * allocated_pa
    expected = raw_expected * park_m * temp_m * wind_m * fb_m * xera_m * platoon_m * statcast_m

    return {
        'jerry_hr_contribution': round(expected, 4),
        'jerry_allocated_pa': round(allocated_pa, 2),
        'jerry_signals': {
            'base_rate': round(regressed_rate, 4),
            'allocated_pa': round(allocated_pa, 2),
            'park_mult': round(park_m, 3),
            'temp_mult': round(temp_m, 3),
            'wind_mult': round(wind_m, 3),
            'pitcher_fb_mult': round(fb_m, 3),
            'pitcher_xera_mult': round(xera_m, 3),
            'platoon_mult': round(platoon_m, 3),
            'statcast_mult': round(statcast_m, 3),
            'raw_contribution': round(raw_expected, 4),
        },
    }
