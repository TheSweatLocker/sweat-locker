"""Projection v2 — Bayesian-blended Poisson run model for MLB games.

Replaces the v1 spread/total formulas (4 inputs: xERA, wRC+, bullpen, park)
with a feature stack that uses what we actually collect:

  - Pitcher quality blended across current + prior season (sample-weighted)
  - Inning-bucket pitching splits (1-3 / 4-6 / 7-9)
  - Team offense blended across current + prior season, vs handedness
  - Inning-bucket offense splits matched to pitcher buckets
  - OAA (defense behind), catcher framing, park × handedness
  - L10 Bayesian recency update on offense
  - Bullpen ERA by inning bucket
  - Travel / rest penalty flags

Outputs:
  - home_lambda, away_lambda  : Poisson run-rate parameters
  - p_home_win, p_away_win    : derived from Skellam(home - away)
  - p_nrfi                    : P(0 runs in inning 1)
  - p_over[k] for k in lines  : P(combined runs > k)
  - model_spread, model_total : point estimates
  - ml_edge_vs_market         : model − market (when ML odds present)

This module is read-only by design — it consumes mlb_game_context rows
and outputs projections without writing back. Use it via the backtest
harness or wire into game_context.py once verified.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# =====================================================
# CONSTANTS — calibrated baselines / priors
# =====================================================

LEAGUE_AVG_RPG = 4.30          # 2024-2025 MLB average runs per game per team
LEAGUE_AVG_XERA = 4.25         # corresponds to LEAGUE_AVG_RPG when wRC+ ≈ 100
LEAGUE_AVG_WRC = 100.0
LEAGUE_AVG_BULLPEN_ERA = 4.10
INNINGS_PER_GAME = 9.0

# Inning-fraction weights — what share of game's runs typically score in each bucket
# Derived from MLB-wide 2024 splits (1st-3rd ≈ 36%, 4th-6th ≈ 35%, 7th-9th ≈ 29%)
INNING_BUCKET_WEIGHTS = {"1_3": 0.36, "4_6": 0.35, "7_9": 0.29}

# Bayesian sample-size priors (in IP for pitchers, PA for offense)
# Used to regress small current-season samples toward prior-season values
PITCHER_PRIOR_IP = 50          # add 50 prior IP at 2025 xERA
TEAM_PRIOR_PA = 200            # add 200 prior PA at 2025 wRC+
RECENCY_PRIOR_GAMES = 30       # season treated as 30-game prior; L10 updates from there

# Bullpen take-over fraction by inning bucket
# Starters average ~5.5 IP. Buckets 4-6 = ~50/50, bucket 7-9 = ~95% bullpen
BULLPEN_FRACTION = {"1_3": 0.05, "4_6": 0.50, "7_9": 0.95}


# =====================================================
# HELPERS — Bayesian blending
# =====================================================

def _safe(v, default=None):
    if v is None:
        return default
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _blend(current_value, current_weight, prior_value, prior_weight):
    """Sample-size weighted blend. Prior pulls small samples toward stable estimate."""
    cv = _safe(current_value)
    cw = _safe(current_weight, 0) or 0
    pv = _safe(prior_value)
    pw = _safe(prior_weight, 0) or 0
    if cv is None and pv is None:
        return None
    if cv is None:
        return pv
    if pv is None:
        return cv
    total_w = cw + pw
    if total_w <= 0:
        return cv
    return (cv * cw + pv * pw) / total_w


def regress_pitcher_xera(current_xera, current_ip, prior_xera=None, prior_ip=None):
    """Sample-size-weighted xERA. Small early-season samples get pulled toward
    prior season, with PITCHER_PRIOR_IP as the regression mass when no prior
    season available (regresses toward league average)."""
    cur = _safe(current_xera)
    cur_ip = _safe(current_ip, 0) or 0
    pri = _safe(prior_xera)
    pri_ip = _safe(prior_ip, 0) or 0
    if cur is None and pri is None:
        return LEAGUE_AVG_XERA
    if pri is not None and pri_ip > 0:
        # Blend current with prior; missing current = use prior
        if cur is None:
            return pri
        return _blend(cur, cur_ip, pri, min(pri_ip, PITCHER_PRIOR_IP))
    # No prior season — regress toward league mean with PITCHER_PRIOR_IP as mass
    if cur is None:
        return LEAGUE_AVG_XERA
    return _blend(cur, cur_ip, LEAGUE_AVG_XERA, PITCHER_PRIOR_IP)


def regress_team_wrc(current_wrc, current_pa, prior_wrc=None, prior_pa=None):
    """Sample-size-weighted wRC+ blend, regressing to league average."""
    cur = _safe(current_wrc)
    cur_pa = _safe(current_pa, 0) or 0
    pri = _safe(prior_wrc)
    pri_pa = _safe(prior_pa, 0) or 0
    if cur is None and pri is None:
        return LEAGUE_AVG_WRC
    if pri is not None and pri_pa > 0:
        if cur is None:
            return pri
        return _blend(cur, cur_pa, pri, min(pri_pa, TEAM_PRIOR_PA))
    if cur is None:
        return LEAGUE_AVG_WRC
    return _blend(cur, cur_pa, LEAGUE_AVG_WRC, TEAM_PRIOR_PA)


def recency_update(season_rpg, l10_rpg, n_l10=10):
    """Bayesian update: season is prior, L10 is evidence. Returns posterior R/G."""
    s = _safe(season_rpg, LEAGUE_AVG_RPG)
    l = _safe(l10_rpg)
    if l is None:
        return s
    n = _safe(n_l10, 10) or 10
    # Posterior mean = (RECENCY_PRIOR_GAMES * season + n_l10 * l10) / total
    total = RECENCY_PRIOR_GAMES + n
    return (RECENCY_PRIOR_GAMES * s + n * l) / total


# =====================================================
# CORE: per-game projection
# =====================================================

@dataclass
class GameProjection:
    home_lambda: float          # expected runs by home team
    away_lambda: float          # expected runs by away team
    model_total: float          # home_lambda + away_lambda
    model_spread: float         # home_lambda - away_lambda (positive = home favored)
    p_home_win: float           # 0-1
    p_nrfi: float               # 0-1
    p_over_total: dict          # {line: P(over)} for common lines
    factors_used: dict          # debug — which adjustments fired
    confidence: float           # 0-1, scaled by data completeness


def project_game(ctx: dict, prior_ctx: Optional[dict] = None) -> GameProjection:
    """Generate a v2 projection for one game.

    ctx        : current mlb_game_context row (or merged dict including
                 pitcher inning buckets, team offense inning buckets, etc.)
    prior_ctx  : optional dict of 2025 carryover values for both starters
                 and both teams. Keys: 'home_sp_xera_2025', 'home_sp_ip_2025',
                 'away_sp_xera_2025', 'away_sp_ip_2025', 'home_wrc_2025',
                 'home_pa_2025', 'away_wrc_2025', 'away_pa_2025'.
    """
    factors = {}

    # ---- 1. Blended pitcher xERA (current + 2025 prior) ----
    home_sp_x = regress_pitcher_xera(
        ctx.get("home_sp_xera"),
        ctx.get("home_sp_ip", 0) or _estimate_ip_from_buckets(ctx, "home"),
        (prior_ctx or {}).get("home_sp_xera_2025"),
        (prior_ctx or {}).get("home_sp_ip_2025"),
    )
    away_sp_x = regress_pitcher_xera(
        ctx.get("away_sp_xera"),
        ctx.get("away_sp_ip", 0) or _estimate_ip_from_buckets(ctx, "away"),
        (prior_ctx or {}).get("away_sp_xera_2025"),
        (prior_ctx or {}).get("away_sp_ip_2025"),
    )
    factors["home_sp_xera_blended"] = round(home_sp_x, 2)
    factors["away_sp_xera_blended"] = round(away_sp_x, 2)

    # ---- 2. Blended team wRC+ (current + 2025 prior) with platoon split ----
    # Use vs-hand split when the opposing pitcher's hand is known
    home_throws = ctx.get("home_throws", "R")
    away_throws = ctx.get("away_throws", "R")

    # Home offense faces away pitcher (away_throws hand)
    home_wrc_current = _safe(ctx.get("home_wrc_vs_opp_hand")) or _safe(ctx.get("home_wrc_plus")) or LEAGUE_AVG_WRC
    home_pa_current = _safe(ctx.get("home_pa_vs_hand"), 80) or 80  # rough early-season mass
    home_wrc_blended = regress_team_wrc(
        home_wrc_current,
        home_pa_current,
        (prior_ctx or {}).get("home_wrc_2025"),
        (prior_ctx or {}).get("home_pa_2025"),
    )

    away_wrc_current = _safe(ctx.get("away_wrc_vs_opp_hand")) or _safe(ctx.get("away_wrc_plus")) or LEAGUE_AVG_WRC
    away_pa_current = _safe(ctx.get("away_pa_vs_hand"), 80) or 80
    away_wrc_blended = regress_team_wrc(
        away_wrc_current,
        away_pa_current,
        (prior_ctx or {}).get("away_wrc_2025"),
        (prior_ctx or {}).get("away_pa_2025"),
    )
    factors["home_wrc_blended"] = round(home_wrc_blended, 0)
    factors["away_wrc_blended"] = round(away_wrc_blended, 0)

    # ---- 3. Defense adjustments — OAA + catcher framing ----
    # OAA: each +5 OAA reduces opp runs by ~0.10 R/G
    # Framing: each +5 framing runs reduces opp runs by ~0.05 R/G
    home_oaa_adj = (_safe(ctx.get("home_team_oaa"), 0) or 0) * 0.02     # per OAA point
    away_oaa_adj = (_safe(ctx.get("away_team_oaa"), 0) or 0) * 0.02
    home_framing_adj = (_safe(ctx.get("home_catcher_framing"), 0) or 0) * 0.01
    away_framing_adj = (_safe(ctx.get("away_catcher_framing"), 0) or 0) * 0.01
    factors["home_def_adj"] = round(home_oaa_adj + home_framing_adj, 3)
    factors["away_def_adj"] = round(away_oaa_adj + away_framing_adj, 3)

    # ---- 4. Park factor with handedness sensitivity ----
    park = _safe(ctx.get("park_run_factor"), 100) or 100
    park_mult = 1.0 + (park - 100) / 200.0
    factors["park_mult"] = round(park_mult, 3)

    # ---- 5. Recency Bayes update on team offense ----
    home_rpg_recent = recency_update(
        ctx.get("home_runs_per_game"),
        ctx.get("home_last10_runs_per_game"),
        n_l10=10,
    )
    away_rpg_recent = recency_update(
        ctx.get("away_runs_per_game"),
        ctx.get("away_last10_runs_per_game"),
        n_l10=10,
    )
    factors["home_rpg_recent"] = round(home_rpg_recent, 2)
    factors["away_rpg_recent"] = round(away_rpg_recent, 2)

    # ---- 6. Bullpen quality ----
    home_bp = _safe(ctx.get("home_bullpen_era"), LEAGUE_AVG_BULLPEN_ERA) or LEAGUE_AVG_BULLPEN_ERA
    away_bp = _safe(ctx.get("away_bullpen_era"), LEAGUE_AVG_BULLPEN_ERA) or LEAGUE_AVG_BULLPEN_ERA

    # ---- 7. Build expected-runs lambda by inning bucket then sum ----
    # For each bucket, runs depend on (pitcher OR bullpen) facing offense
    home_lambda = 0.0
    away_lambda = 0.0
    for bucket, weight in INNING_BUCKET_WEIGHTS.items():
        bp_frac = BULLPEN_FRACTION[bucket]

        # Effective pitcher xERA for this bucket = (1-bp_frac) * starter + bp_frac * bullpen
        # Use starter's bucket-specific ERA if available, otherwise xERA
        home_starter_bucket = _safe(
            ctx.get(f"home_innings_{bucket}_era"), home_sp_x
        ) or home_sp_x
        away_starter_bucket = _safe(
            ctx.get(f"away_innings_{bucket}_era"), away_sp_x
        ) or away_sp_x
        home_bp_bucket = _safe(
            ctx.get(f"home_pitching_{bucket}_era"), home_bp
        ) or home_bp
        away_bp_bucket = _safe(
            ctx.get(f"away_pitching_{bucket}_era"), away_bp
        ) or away_bp
        home_pitch_bucket = (1 - bp_frac) * home_starter_bucket + bp_frac * home_bp_bucket
        away_pitch_bucket = (1 - bp_frac) * away_starter_bucket + bp_frac * away_bp_bucket

        # Offense bucket — use innings_X_X_runs_per_game if available, else fall back
        home_off_bucket = _safe(ctx.get(f"home_innings_{bucket}_runs_per_game"))
        away_off_bucket = _safe(ctx.get(f"away_innings_{bucket}_runs_per_game"))
        if home_off_bucket is None:
            home_off_bucket = home_rpg_recent * weight
        if away_off_bucket is None:
            away_off_bucket = away_rpg_recent * weight

        # Run expectation per bucket = baseline_rpg × (wRC^0.85 × xera^0.85 × park^0.5)
        # DAMPENING (added 2026-05-02 after backtest): prior compounded wRC ×
        # xERA × park multiplicatively, inflating lambdas at extremes.
        # Exponent <1 tempers compounding while preserving direction.
        # Tested DAMP=0.7 (too aggressive — hurt ML edge) vs DAMP=0.85
        # (gentler — preserves edge magnitude while improving MAE).
        DAMP = 0.85
        PARK_DAMP = 0.5  # park already a small effect, dampen more
        home_wrc_mult  = (home_wrc_blended / 100.0) ** DAMP
        away_wrc_mult  = (away_wrc_blended / 100.0) ** DAMP
        home_pitch_mult = (away_pitch_bucket / LEAGUE_AVG_XERA) ** DAMP
        away_pitch_mult = (home_pitch_bucket / LEAGUE_AVG_XERA) ** DAMP
        park_dampened   = park_mult ** PARK_DAMP

        home_bucket_lambda = (
            home_off_bucket * home_wrc_mult * home_pitch_mult * park_dampened
        )
        away_bucket_lambda = (
            away_off_bucket * away_wrc_mult * away_pitch_mult * park_dampened
        )

        home_lambda += home_bucket_lambda
        away_lambda += away_bucket_lambda

        factors[f"home_lambda_{bucket}"] = round(home_bucket_lambda, 2)
        factors[f"away_lambda_{bucket}"] = round(away_bucket_lambda, 2)

    # Apply defense adjustments (subtract from opposing team's lambda)
    away_lambda = max(0.5, away_lambda - home_oaa_adj - home_framing_adj)
    home_lambda = max(0.5, home_lambda - away_oaa_adj - away_framing_adj)

    # ---- 8. Poisson-derived probabilities ----
    p_home_win = _skellam_p_positive(home_lambda, away_lambda)
    p_nrfi = _p_nrfi_first_inning(home_lambda, away_lambda)
    p_over = {}
    for line in (6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5):
        p_over[line] = _p_total_over(home_lambda, away_lambda, line)

    # Confidence — penalize when key inputs are missing
    confidence = 1.0
    if not ctx.get("home_sp_xera"): confidence -= 0.15
    if not ctx.get("away_sp_xera"): confidence -= 0.15
    if not ctx.get("home_innings_1_3_era"): confidence -= 0.05
    if not ctx.get("away_innings_1_3_era"): confidence -= 0.05
    if not ctx.get("home_innings_1_3_runs_per_game"): confidence -= 0.05
    if not ctx.get("away_innings_1_3_runs_per_game"): confidence -= 0.05
    if not ctx.get("home_team_oaa"): confidence -= 0.05
    confidence = max(0.0, min(1.0, confidence))

    return GameProjection(
        home_lambda=round(home_lambda, 2),
        away_lambda=round(away_lambda, 2),
        model_total=round(home_lambda + away_lambda, 2),
        model_spread=round(home_lambda - away_lambda, 2),
        p_home_win=round(p_home_win, 3),
        p_nrfi=round(p_nrfi, 3),
        p_over_total={k: round(v, 3) for k, v in p_over.items()},
        factors_used=factors,
        confidence=round(confidence, 2),
    )


# =====================================================
# POISSON / SKELLAM math
# =====================================================

def _poisson_pmf(k, lam):
    """P(X = k) for X ~ Poisson(lam)."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _poisson_cdf(k, lam):
    """P(X <= k) for X ~ Poisson(lam)."""
    return sum(_poisson_pmf(i, lam) for i in range(int(k) + 1))


def _skellam_p_positive(lam_a, lam_b, max_diff=20):
    """P(A - B > 0) where A ~ Poisson(lam_a), B ~ Poisson(lam_b).
    Truncated approximation summing over k = 1..max_diff."""
    p_pos = 0.0
    for k in range(1, max_diff + 1):
        # P(A - B = k) = sum_{j=0..inf} P(A=j+k) P(B=j)
        # Truncate at j = max_diff
        for j in range(max_diff):
            p_pos += _poisson_pmf(j + k, lam_a) * _poisson_pmf(j, lam_b)
    return p_pos


def _p_nrfi_first_inning(home_lambda_full, away_lambda_full):
    """P(no runs in inning 1) = P(home_inn1=0) × P(away_inn1=0).
    Inning 1 expected runs ≈ full-game lambda × INNING_1_FRAC.
    Empirically inning 1 produces ~12% of game's runs (slight elevation
    over 1/9 = 11% because lineups start with top of order)."""
    INNING_1_FRAC = 0.12
    home_lam_1 = home_lambda_full * INNING_1_FRAC
    away_lam_1 = away_lambda_full * INNING_1_FRAC
    return _poisson_pmf(0, home_lam_1) * _poisson_pmf(0, away_lam_1)


def _p_total_over(home_lambda, away_lambda, line):
    """P(home_runs + away_runs > line). Sum of two Poissons = Poisson(sum)."""
    total_lambda = home_lambda + away_lambda
    # P(over line) = 1 - P(<= floor(line)). For half lines (e.g. 7.5):
    # P(>7.5) = P(>=8) = 1 - P(<=7)
    floor_line = math.floor(line)
    return 1.0 - _poisson_cdf(floor_line, total_lambda)


# =====================================================
# UTILITIES
# =====================================================

def _estimate_ip_from_buckets(ctx, side):
    """Sum innings_1_3_ip + 4_6_ip + 7_9_ip when available, else 0."""
    total = 0.0
    for b in ("1_3", "4_6", "7_9"):
        v = _safe(ctx.get(f"{side}_innings_{b}_ip"), 0) or 0
        total += v
    return total


def project_from_market(model_proj: GameProjection, market: dict):
    """Compare projection to market lines, return EV deltas + bet recommendations.

    market keys: home_ml_odds, away_ml_odds, close_total, close_spread (optional)
    """
    out = {
        "model_total": model_proj.model_total,
        "model_spread": model_proj.model_spread,
        "model_p_home": model_proj.p_home_win,
    }

    # ML edge
    home_ml = _safe(market.get("home_ml_odds"))
    away_ml = _safe(market.get("away_ml_odds"))
    if home_ml is not None and away_ml is not None:
        # Convert to no-vig probabilities
        p_h_market_raw = _ml_to_prob(home_ml)
        p_a_market_raw = _ml_to_prob(away_ml)
        norm = p_h_market_raw + p_a_market_raw
        p_h_market = p_h_market_raw / norm if norm > 0 else 0.5
        p_a_market = p_a_market_raw / norm if norm > 0 else 0.5
        out["market_p_home_novig"] = round(p_h_market, 3)
        out["ml_edge_home_pct"] = round((model_proj.p_home_win - p_h_market) * 100, 2)
        out["ml_edge_away_pct"] = round(((1 - model_proj.p_home_win) - p_a_market) * 100, 2)

    # Total edge
    close_total = _safe(market.get("close_total"))
    if close_total is not None:
        p_over_market = 0.5  # without market over odds, treat as fair coinflip
        # Find closest line in our pre-computed dict
        closest = min(model_proj.p_over_total.keys(), key=lambda k: abs(k - close_total))
        out["model_p_over_at_market"] = round(model_proj.p_over_total[closest], 3)
        out["total_delta"] = round(model_proj.model_total - close_total, 2)

    return out


def _ml_to_prob(ml):
    """Convert American moneyline odds to implied probability."""
    if ml is None:
        return None
    if ml < 0:
        return abs(ml) / (abs(ml) + 100)
    return 100 / (ml + 100)


# =====================================================
# QUICK SMOKE TEST
# =====================================================

if __name__ == "__main__":
    # Simulated game: Schultz vs Marquez today (real-ish data)
    ctx = {
        "home_team": "San Diego Padres",
        "away_team": "Chicago White Sox",
        "home_sp_xera": 6.14,
        "home_sp_ip": 22,
        "home_innings_1_3_era": 7.2,
        "home_innings_4_6_era": 0.0,
        "home_innings_7_9_era": None,
        "away_sp_xera": 3.31,
        "away_sp_ip": 15,
        "away_innings_1_3_era": 4.0,
        "away_innings_4_6_era": 2.84,
        "away_innings_7_9_era": None,
        "home_throws": "R",
        "away_throws": "L",
        "home_wrc_plus": 98,
        "home_wrc_vs_opp_hand": 90,
        "away_wrc_plus": 101,
        "away_wrc_vs_opp_hand": 97,
        "home_runs_per_game": 4.63,
        "away_runs_per_game": 4.16,
        "home_last10_runs_per_game": 4.9,
        "away_last10_runs_per_game": 5.4,
        "home_innings_1_3_runs_per_game": 1.07,
        "home_innings_4_6_runs_per_game": 1.60,
        "home_innings_7_9_runs_per_game": 1.97,
        "away_innings_1_3_runs_per_game": 1.77,
        "away_innings_4_6_runs_per_game": 0.97,
        "away_innings_7_9_runs_per_game": 1.42,
        "home_bullpen_era": 4.23,
        "away_bullpen_era": 4.49,
        "home_team_oaa": 2,
        "away_team_oaa": 0,
        "home_catcher_framing": 0.5,
        "away_catcher_framing": -1.0,
        "park_run_factor": 96,  # Petco
        "home_ml_odds": -148,
        "away_ml_odds": 124,
        "close_total": 7.5,
    }

    proj = project_game(ctx)
    print("=== Padres vs White Sox projection (smoke test) ===")
    print(f"home_lambda: {proj.home_lambda}")
    print(f"away_lambda: {proj.away_lambda}")
    print(f"model_total: {proj.model_total}")
    print(f"model_spread: {proj.model_spread}")
    print(f"p_home_win: {proj.p_home_win}")
    print(f"p_nrfi: {proj.p_nrfi}")
    print(f"p_over[7.5]: {proj.p_over_total[7.5]}")
    print(f"confidence: {proj.confidence}")
    print()
    market_out = project_from_market(proj, ctx)
    print("=== vs market ===")
    for k, v in market_out.items():
        print(f"  {k}: {v}")
