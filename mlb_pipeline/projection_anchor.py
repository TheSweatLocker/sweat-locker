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
) -> tuple[Optional[float], Optional[float], str]:
    """Return (anchored_spread, weight, reason).

    - anchored_spread: the blended value (falls back to projected_spread
      when the anchor doesn't fire, or None if inputs are missing).
    - weight: the anchor weight applied (0.0-1.0), or None if no anchor.
    - reason: short human-readable string for the audit trail.

    The caller should overwrite `projected_spread` on the ctx row with the
    returned anchored value, and store the raw projection under
    `projected_spread_raw` for grading.
    """
    if projected_spread is None:
        return None, None, 'no_projection'
    if market_spread is None:
        # Can't anchor without a market number — pass model through raw.
        return projected_spread, 0.0, 'no_market_line'
    if stats_source != 'prior_season_regressed':
        # Model is on live current-season data — no anchor needed.
        return projected_spread, 0.0, f'stats_source={stats_source} · no_anchor'

    delta = abs(market_spread - projected_spread)
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

    anchored = w * market_spread + (1 - w) * projected_spread
    return round(anchored, 2), w, f'{tier} · w={w:.2f}'
