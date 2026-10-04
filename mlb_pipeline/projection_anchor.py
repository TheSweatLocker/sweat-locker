"""Market-anchor projection layer for football spread models.

Context
-------
Andy audit 9/12: NFL Week 1 model output for ARI @ LAC read "Arizona ahead
by 2" against a market of LAC -9.5. Root cause: stats_source='prior_season_
regressed' — thin 2026 sample forced fallback to 2025 stats regressed 50/50
toward league mean, mashing every team's rating toward the middle. Model
CAN'T see genuine team-quality signal early season, so its magnitude
projections drift wildly from market on high-conviction spreads. Same class
of bug shows on NCAAF Weeks 1-3.

Fix
---
Blend `projected_spread` toward `close_spread` (market) by a weight that
scales with disagreement magnitude:

    |Δ|       weight    Interpretation
    ≤ 3       0.00      Real edge zone — trust the model
    3-6       0.40      Some skepticism — light anchor
    > 6       0.75      Hallucination gate — heavy anchor

Rationale for the tiers
-----------------------
Small disagreements (≤3 pts) are inside the noise floor of any football
projection — no anchor needed. Medium disagreements (3-6 pts) are the sweet
spot where our model MIGHT have real edge but might also just be miscalibrated
— a 40% anchor takes 40% off the disagreement without killing it. Large
disagreements (>6 pts) on thin-sample early-season data almost always trace
to a broken team-stat input (regressed-to-mean, injury blind spot, coaching
change), not real edge — anchor 75% to keep the pick's direction but bring
magnitude in line with market. The one game where the model IS actually
right about a 10-pt disagreement will still project on the correct side
(75% market + 25% model on a 10-pt gap still leaves 2.5 pts of model tilt).

Only fires when stats_source='prior_season_regressed'. Once we're past Wk 4
(cur_games ≥ BLEND_UNTIL_GAMES for a sport), current-season data drives the
projection and the anchor becomes a no-op — model gets full authority back.

Next iteration (Wk 2+): layer a per-team learned residual on top of this
tier. After each week grades, compute (market_spread - actual_margin) per
team and roll it into a team-level bias correction so the anchor weight
becomes data-driven instead of universal.

Called from nfl_game_context.build_context_row and
ncaaf_game_context.build_context_row after compute_projections.

Fields written to ctx row
-------------------------
    projected_spread          → the anchored value (used by ensemble scorer)
    projected_spread_raw      → the pre-anchor value (audit trail)
    spread_anchor_weight      → the tier weight that fired (0.0 - 0.75)
    spread_anchor_reason      → short string explaining which tier

See migration 20260912c_projection_anchor_columns.sql.
"""
from __future__ import annotations
from typing import Optional


# 2026-09-13 tiered weights. These are the initial "regret-minimization"
# tiers Andy signed off on. After Wk 1 grades come in, we'll validate
# against real outcomes and tune (or add a per-team learned overlay).
_TIER_LOW_MAX  = 3.0
_TIER_MED_MAX  = 6.0
_W_LOW         = 0.0     # ≤3 pts   → trust model
_W_MED         = 0.4     # 3-6 pts  → light anchor
_W_HIGH        = 0.75    # >6 pts   → heavy anchor / hallucination gate


def anchor_projected_spread(
    market_spread: Optional[float],
    projected_spread: Optional[float],
    stats_source: Optional[str],
    sport: Optional[str] = None,
) -> tuple[Optional[float], Optional[float], str]:
    """Return (anchored_spread, weight, reason).

    - anchored_spread: the blended value (falls back to projected_spread
      when the anchor doesn't fire, or None if inputs are missing).
    - weight: the anchor weight applied (0.0-1.0), or None if no anchor.
    - reason: short human-readable string for the audit trail.

    The caller should overwrite `projected_spread` on the ctx row with the
    returned anchored value, and store the raw projection under
    `projected_spread_raw` for grading.

    2026-09-16 SPORT-AWARE SIGN NORMALIZATION (Andy MIA@WF audit).
    Root cause: close_spread and projected_spread use DIFFERENT sign
    conventions for NCAAF (and MLB) but the anchor was blending them raw.
      close_spread    NCAAF: positive = AWAY favored (per project_close_spread_sign_bug_914)
      close_spread    NFL:   positive = HOME favored
      projected_spread ALL:  positive = HOME favored (from sp_gap * K)
    MIA@WF: close_spread=+21 (MIA fav), projected_spread=-3.575 (WF loses by 3.575
    → same direction, home-relative). Anchor computed delta=|21-(-3.575)|=24.575
    → HIGH tier w=0.75 → anchored = 0.75*21 + 0.25*-3.575 = +14.86.
    +14.86 in home-relative convention = WF favored by 14.86 = literal SIGN FLIP.
    Fix: for NCAAF (and any sport where close_spread flips vs projected_spread),
    normalize market_spread to the projected_spread convention BEFORE blending,
    so we're always blending like-with-like.
    """
    if projected_spread is None:
        return None, None, 'no_projection'
    if market_spread is None:
        # Can't anchor without a market number — pass model through raw.
        return projected_spread, 0.0, 'no_market_line'
    if stats_source != 'prior_season_regressed':
        # Model is on live current-season data — no anchor needed.
        return projected_spread, 0.0, f'stats_source={stats_source} · no_anchor'

    # Normalize market_spread into the projected_spread convention
    # (positive = home favored). NCAAF and MLB store close_spread with
    # positive = away favored, so flip. NFL already matches.
    sport_up = (sport or '').upper()
    if sport_up in ('NCAAF', 'MLB'):
        market_norm = -market_spread
    else:
        market_norm = market_spread

    delta = abs(market_norm - projected_spread)
    if delta <= _TIER_LOW_MAX:
        w = _W_LOW
        tier = f'low_Δ={delta:.1f}'
    elif delta <= _TIER_MED_MAX:
        w = _W_MED
        tier = f'med_Δ={delta:.1f}'
    else:
        w = _W_HIGH
        tier = f'high_Δ={delta:.1f}'

    if w == 0.0:
        return projected_spread, 0.0, f'{tier} · no_anchor'

    anchored = w * market_norm + (1 - w) * projected_spread
    return round(anchored, 2), w, f'{tier} · w={w:.2f}'


# ══════════════════════════════════════════════════════════════════════════
# 2026-10-03 · DISPERSION CALIBRATION
# ══════════════════════════════════════════════════════════════════════════
# Measured on every graded 2026 NFL game that carries both a projection and a
# closing line (n=47):
#
#     sd(model projection)        2.87 raw / 5.00 anchored
#     sd(market-implied margin)   6.58
#     sd(actual home margin)     13.90
#
# The model is far narrower than the market. Differencing a narrow predictor
# against a wide one does not produce an edge — it produces a SYSTEMATIC tilt
# toward whichever side the wide predictor is extreme on. That side is always
# the underdog. Hence the model leaned dog on 40 of 47 graded games (85.1%)
# and on 12 of 15 of the 2026-10-04 slate (80%).
#
# THE DOG BIAS IS THIS ARITHMETIC, not a view about underdogs. It cannot be
# tuned away by changing thresholds, because every threshold is applied to a
# quantity that is already tilted.
#
# WHY IT GOT WORSE, NOT BETTER, AT WEEK 4. The market anchor above only fires
# while stats_source == 'prior_season_regressed'. Once current-season stats
# take over, the anchor becomes a no-op and the model gets FULL authority —
# while still being ~37% too narrow. So the compression is unmitigated
# exactly when the model is trusted most. Verified on the 10-04 slate: every
# row has spread_anchor_weight 0.0 and sd(projected) 3.23 vs market 5.16.
#
# THE CORRECTION compares each game's position WITHIN its own distribution
# rather than in raw points. A game is a real disagreement only when the model
# ranks it differently from the market, not merely because the model is
# narrower. Scaling the model's deviation to the market's dispersion achieves
# that while preserving the model's ordering completely.
#
# WHAT THIS DOES NOT DO — and this matters more than the fix. In a joint
# regression of actual margin on (market, model) over the same 47 games the
# model's coefficient is -0.067 anchored and -0.895 raw: given the closing
# line, our projection carries no positive information. Calibration makes the
# residual SYMMETRIC; it cannot make it PREDICTIVE. Real NFL edge needs an
# independent feature set (opponent-adjusted EPA), not a rescaling.
_MAX_SCALE = 2.0      # never more than double a projection
_MIN_N = 6            # below this a slate sd is noise


def calibrate_dispersion(projections, market_margins, max_scale: float = _MAX_SCALE):
    """Scale projections so their spread matches the market's.

    Both sequences must be in the SAME sense (expected home margin) and
    aligned by index; None entries are ignored when fitting and passed
    through unscaled.

    Returns (scaled_projections, scale, note).
    """
    pairs = [(p, m) for p, m in zip(projections, market_margins)
             if p is not None and m is not None]
    if len(pairs) < _MIN_N:
        return list(projections), 1.0, f'n={len(pairs)} too thin to calibrate'

    def _sd(xs):
        n = len(xs)
        mu = sum(xs) / n
        return (sum((x - mu) ** 2 for x in xs) / n) ** 0.5, mu

    sd_p, mu_p = _sd([p for p, _ in pairs])
    sd_m, mu_m = _sd([m for _, m in pairs])
    if sd_p <= 1e-6:
        # A model emitting a near-constant cannot be rescued by scaling, and
        # multiplying ~0 spread by a huge factor would amplify pure noise.
        return list(projections), 1.0, 'model dispersion ~0 — not calibrated'
    scale = min(max_scale, sd_m / sd_p)
    # Re-centre on the MARKET's mean, not the model's. Scaling around the
    # model's own centre leaves any mean offset intact, and that offset is a
    # second, independent source of one-sided lean: on the 10-04 slate,
    # matching dispersion alone moved the dog lean only 80% -> 73%. Matching
    # both moments makes the residual a pure RANK disagreement — the model
    # and the market now describe the same distribution, so an edge exists
    # only where they order the slate differently, which is the only kind of
    # disagreement a compressed model can legitimately claim.
    out = [None if p is None else round(mu_m + (p - mu_p) * scale, 2)
           for p in projections]
    return (out, round(scale, 3),
            f'scaled x{scale:.2f} (sd {sd_p:.2f}->{sd_m:.2f}, '
            f'mean {mu_p:+.2f}->{mu_m:+.2f})')
