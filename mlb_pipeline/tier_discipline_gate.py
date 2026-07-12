"""Tier-discipline publishing gate for total picks.

Derived from walk-forward tier-sliced backtest (n=872 graded games).
The gate decides whether a game is card-eligible based on:

  - ALL-3 model unity (v3+v4+jerry must agree on direction)
  - Composite gap magnitude (gap_proj must clear specific bands)
  - Direction-specific rules (OVER vs UNDER have different sweet spots)

Validated tier hit rates (walk-forward, season-to-date):

  OVER side:
    PRIME-OVER  : all-3 OVER + gap 2.0-3.0  → 71% (n=24)
    LEAN-OVER   : all-3 OVER + gap 0.5-1.5  → 65% (n=17)
    ELITE-OVER  : all-3 OVER + gap 1.5+ + snc>=3 → 67% (n=6, small)

  UNDER side:
    ELITE-UNDER : gap_proj <= -3.0 (any model agreement) → 64% (n=25)
    PRIME-UNDER : all-3 UNDER + gap <= -2.0  → 100% (n=2, tiny)

  Foundation:
    ALL-3 unanimous (any direction) → 58% (n=78)

Filter rules (in order of priority):

  PUBLISH = ELITE if:
    - Side+magnitude qualifies as one of the 71%/64% tiers above

  PUBLISH = STRONG if:
    - ALL-3 unanimous + gap_composite magnitude >= 1.5

  PUBLISH = LEAN if:
    - ALL-3 unanimous + gap magnitude 0.5-1.5

  SKIP if:
    - 2-of-3 only (coinflip in backtest)
    - mild magnitude (< 0.5 gap)
    - middle UNDER (gap -1.5 to -3.0)  [42-44% historical loser]

  IGNORE: signal_confluence_net.
    Walk-forward shows 0.9% feature importance + no monotonic predictive
    relationship. Was previously a primary resolver driver. Now context only.
"""
from dataclasses import dataclass
from typing import Optional, List, Dict, Any


@dataclass
class TierVerdict:
    tier: str          # 'ELITE' | 'STRONG' | 'LEAN' | 'SKIP'
    direction: Optional[str]  # 'OVER' | 'UNDER' | None
    reason: str
    composite_gap: Optional[float]
    models_agree: int  # how many of v3/v4/jerry agreed
    historical_hit_rate: Optional[float]  # expected hit rate from backtest


def _models_direction(proj_total, v4_total, jerry_total, line):
    """Count how many models lean each direction."""
    overs = 0
    unders = 0
    for m in (proj_total, v4_total, jerry_total):
        if m is None or line is None:
            continue
        gap = float(m) - float(line)
        if gap > 0.3:
            overs += 1
        elif gap < -0.3:
            unders += 1
    return overs, unders


def evaluate_total(
    *,
    line: float,
    proj_total: Optional[float],
    v4_total: Optional[float],
    jerry_total: Optional[float],
    panel_implied_total: Optional[float] = None,
) -> TierVerdict:
    """Apply tier-discipline rules to decide whether a total pick publishes.

    2026-06-24 — added Panel-implied total as a 4th vote (optional).
    Backtest on 291 games showed Panel beats Composite 54-46 when they
    DISAGREE. Panel ELITE-tier (gap >=3) hits 70%. Panel acts as a
    tie-breaker when v3/v4/jerry split, and required for 4-WAY ELITE tier.
    """
    if line is None or proj_total is None:
        return TierVerdict('SKIP', None, 'missing anchor data (line/proj)', None, 0, None)

    line = float(line)
    proj_total = float(proj_total)
    v4 = float(v4_total) if v4_total is not None else None
    jerry = float(jerry_total) if jerry_total is not None else None
    panel = float(panel_implied_total) if panel_implied_total is not None else None

    overs, unders = _models_direction(proj_total, v4, jerry, line)
    has_v4_jerry = (v4 is not None) and (jerry is not None)
    composite_avg = sum(filter(None, [proj_total, v4, jerry])) / max(1, sum(1 for x in [proj_total, v4, jerry] if x is not None))
    gap = composite_avg - line

    # Panel direction relative to line (2026-06-24)
    panel_dir = None
    if panel is not None:
        if panel > line + 0.3:
            panel_dir = 'OVER'
        elif panel < line - 0.3:
            panel_dir = 'UNDER'
        else:
            panel_dir = 'NEU'

    # Hard SKIPs first
    if abs(gap) < 0.5:
        return TierVerdict('SKIP', None, f'composite gap |{gap:+.2f}| < 0.5 — no signal', gap, max(overs, unders), None)

    # ===== Panel-aware counter-signal rejection (2026-06-24) =====
    # Backtest: when Panel ML margin <0.5 runs, Panel ML is wrong 60% of
    # the time. For totals, a Panel-neutral signal contradicting a loud
    # composite is a coinflip trap. If composite says OVER/UNDER but Panel
    # says NEU (basically at line), downgrade the conviction.
    if panel is not None and panel_dir == 'NEU' and abs(gap) >= 1.5:
        return TierVerdict('SKIP', None,
            f'composite says {abs(gap):.1f}-run gap but Panel implied {panel:.1f} is at line — coinflip trap',
            gap, max(overs, unders), None)

    # Panel-disagrees flip: if Panel direction is OPPOSITE composite direction,
    # historical edge favors Panel 54-46. Instead of throwing the pick away
    # (POTD noPlay for 6+ days 7/6-7/11 was caused by this), publish Panel's
    # direction as LEAN when Panel gap is loud enough. 2026-07-12 patch.
    if panel_dir and panel_dir != 'NEU':
        composite_dir = 'OVER' if gap > 0 else 'UNDER'
        if panel_dir != composite_dir:
            panel_gap = (panel or 0) - line
            # Threshold set to 1.0 (was 1.5) on 2026-07-12 — 7/11 audit showed
            # Panel-agree UNDER calls with gaps 1.0-1.5 (CHC/CIN, PHI/DET) all
            # hit UNDER. Panel edge is 54% on disagreements, so 1.0-run gap
            # is enough of a signal.
            if abs(panel_gap) >= 1.0:
                return TierVerdict('LEAN', panel_dir,
                    f'composite {composite_dir} but Panel {panel_dir} loud ({panel_gap:+.1f}) — publish Panel (54% edge)',
                    panel_gap, 0, 0.54)
            return TierVerdict('SKIP', None,
                f'composite {composite_dir} vs Panel {panel_dir} but Panel gap {panel_gap:+.1f} thin — skip',
                gap, max(overs, unders), None)

    # ===== UNDER side =====
    if gap < 0:
        # ELITE-4WAY UNDER: all 3 composite UNDER + Panel UNDER + gap <= -3
        # Highest conviction tier (4-way unanimity at elite magnitude).
        # Historical Panel ELITE-UNDER hits 71% (n=7) — small sample but
        # combined with all-3 composite agreement, this is the loudest signal.
        if (proj_total - line <= -3.0 and unders >= 3 and
            panel_dir == 'UNDER'):
            return TierVerdict('ELITE', 'UNDER',
                f'4-way ELITE UNDER: all-3 + Panel agree, proj gap {proj_total - line:.2f}',
                gap, unders, 0.71)

        # ELITE UNDER: any setup with proj-vs-line gap <= -3.0
        if proj_total - line <= -3.0:
            return TierVerdict('ELITE', 'UNDER',
                f'proj_total - line = {proj_total - line:.2f} (elite under signal)',
                gap, unders, 0.64)

        # STRONG UNDER: needs all-3 + gap <= -1.5 (small sample but consistent)
        if unders >= 3 and gap <= -1.5:
            return TierVerdict('STRONG', 'UNDER',
                f'all-3 UNDER + composite gap {gap:.2f}',
                gap, unders, 0.60)

        # KILL zone: gap -1.5 to -3.0 historically LOSES (44%, 42%). Skip.
        if -3.0 < gap < -1.5:
            return TierVerdict('SKIP', None,
                f'gap {gap:.2f} sits in middle-UNDER loser band (42-44% hist)',
                gap, unders, None)

        # LEAN UNDER: all-3 + lighter gap
        if unders >= 3 and gap >= -1.5:
            return TierVerdict('LEAN', 'UNDER',
                f'all-3 UNDER + light gap {gap:.2f}',
                gap, unders, 0.57)

        return TierVerdict('SKIP', None,
            f'UNDER lean but no all-3 unity (only {unders}/3 agree)',
            gap, unders, None)

    # ===== OVER side =====
    if gap > 0:
        # ELITE-4WAY OVER: all-3 composite OVER + Panel OVER + gap >= 2
        # Highest conviction OVER tier. Panel ELITE-tier hit 70% in
        # backtest; combined with all-3 unanimity this is the loudest OVER.
        if gap >= 2.0 and overs >= 3 and panel_dir == 'OVER':
            return TierVerdict('ELITE', 'OVER',
                f'4-way ELITE OVER: all-3 + Panel agree, composite gap {gap:.2f}',
                gap, overs, 0.75)

        # PRIME OVER: gap 2.0-3.0 + all-3 (71% hist)
        if 2.0 <= gap < 3.0 and overs >= 3:
            return TierVerdict('PRIME', 'OVER',
                f'all-3 OVER + composite gap {gap:.2f} (prime band 71% hist)',
                gap, overs, 0.71)

        # ELITE OVER: very loud + all-3 (only sample-thin band)
        if gap >= 3.0 and overs >= 3:
            return TierVerdict('ELITE', 'OVER',
                f'all-3 OVER + elite gap {gap:.2f}',
                gap, overs, 0.62)

        # STRONG OVER: all-3 + gap 1.2-2.0 (57% hist - marginal)
        if 1.2 <= gap < 2.0 and overs >= 3:
            return TierVerdict('STRONG', 'OVER',
                f'all-3 OVER + composite gap {gap:.2f}',
                gap, overs, 0.57)

        # LEAN OVER: all-3 + gap 0.7-1.2 (56% hist)
        if 0.7 <= gap < 1.2 and overs >= 3:
            return TierVerdict('LEAN', 'OVER',
                f'all-3 OVER + light gap {gap:.2f}',
                gap, overs, 0.56)

        # mild magnitude (< 0.7) historically loses (48%) — skip
        if gap < 0.7:
            return TierVerdict('SKIP', None,
                f'gap {gap:.2f} < 0.7 (mild-OVER 48% hist loser)',
                gap, overs, None)

        # OVER lean without all-3 — skip
        return TierVerdict('SKIP', None,
            f'OVER lean but no all-3 unity (only {overs}/3 agree)',
            gap, overs, None)

    return TierVerdict('SKIP', None, 'no direction', gap, 0, None)


# ─────────────────────────────────────────────────────────────────────────
# ML / SIDE DISCIPLINE GATE (2026-06-24)
# ─────────────────────────────────────────────────────────────────────────
# POTD ML picks were bleeding alongside totals (CHC 6/20, SF 6/9, BAL 6/7,
# SF 6/6 — multiple resolver STRONG ML losses). Resolver tier alone isn't
# sufficient — same pattern as composite over-bias for totals.
#
# Backtest on 291 jerry_cache games:
#   Panel ML margin >= 3.0:  75% hit (n=12)
#   Panel ML margin 2-3:     62% hit (n=29)
#   Panel ML margin 1-2:     53% hit (n=91)
#   Panel ML margin 0.5-1:   57% hit (n=70)
#   Panel ML margin < 0.5:   40% hit (n=89) ← counter-signal, fade
#
# Gate rules (parallel structure to total gate):
#   ELITE: resolver ELITE + Panel margin >= 2.0 + composite spread agrees
#   STRONG: resolver STRONG + Panel margin >= 1.0 + composite spread agrees
#   LEAN: resolver STRONG + Panel margin >= 0.5
#   SKIP: resolver below STRONG, OR Panel margin < 0.5 (counter-signal),
#         OR Panel direction OPPOSITE resolver direction
#
# composite_spread positive = HOME wins by that margin

def evaluate_ml(
    *,
    resolver_tier: Optional[str],         # 'ELITE' | 'STRONG' | 'LEAN' | 'LIGHT' | 'SKIP'
    resolver_direction: Optional[str],    # 'HOME' | 'AWAY'
    composite_spread: Optional[float],    # avg of v3+v4+jerry spreads (+ = home wins)
    panel_implied_margin: Optional[float] = None,  # +home runs - away runs (Panel)
) -> TierVerdict:
    """Apply tier-discipline rules to decide whether an ML/RL pick publishes.

    Returns SKIP for anything that doesn't pass the resolver + Panel + composite
    triple check. POTD selector reads tier verdict and rejects pick if SKIP.
    """
    if resolver_tier in (None, 'SKIP', 'LIGHT'):
        return TierVerdict('SKIP', None,
            f'resolver tier {resolver_tier} below STRONG threshold',
            None, 0, None)
    if resolver_direction not in ('HOME', 'AWAY'):
        return TierVerdict('SKIP', None, 'resolver has no clean direction',
            None, 0, None)

    # Panel ML margin counter-signal — when margin is tight, Panel ML hits
    # only 40%. Even with STRONG resolver, that's a coinflip trap.
    if panel_implied_margin is not None:
        abs_margin = abs(panel_implied_margin)
        if abs_margin < 0.5:
            return TierVerdict('SKIP', None,
                f'Panel margin {panel_implied_margin:+.1f} too tight (40% hist on <0.5 margin)',
                None, 0, None)
        # Panel direction relative to resolver
        panel_winner = 'HOME' if panel_implied_margin > 0 else 'AWAY'
        if panel_winner != resolver_direction:
            return TierVerdict('SKIP', None,
                f'resolver says {resolver_direction} but Panel margin {panel_implied_margin:+.1f} says {panel_winner}',
                None, 0, None)

    # Composite spread agreement
    composite_winner = None
    if composite_spread is not None:
        try:
            cs = float(composite_spread)
            if cs > 0.3:
                composite_winner = 'HOME'
            elif cs < -0.3:
                composite_winner = 'AWAY'
        except (TypeError, ValueError):
            pass
    if composite_winner and composite_winner != resolver_direction:
        return TierVerdict('SKIP', None,
            f'resolver {resolver_direction} but composite spread {composite_spread:+.2f} → {composite_winner}',
            None, 0, None)

    # ELITE: resolver ELITE + Panel margin >= 2 (when Panel available)
    if resolver_tier == 'ELITE':
        if panel_implied_margin is None or abs(panel_implied_margin) >= 2.0:
            return TierVerdict('ELITE', resolver_direction,
                f'resolver ELITE + Panel margin {panel_implied_margin or "n/a"}',
                None, 0, 0.75)
        return TierVerdict('STRONG', resolver_direction,
            f'resolver ELITE but Panel margin {panel_implied_margin:+.1f} only mild — downgrade to STRONG',
            None, 0, 0.62)

    # STRONG: resolver STRONG + Panel margin >= 1.0
    if resolver_tier == 'STRONG':
        if panel_implied_margin is None or abs(panel_implied_margin) >= 1.0:
            return TierVerdict('STRONG', resolver_direction,
                f'resolver STRONG + Panel margin {panel_implied_margin or "n/a"}',
                None, 0, 0.62)
        # Panel margin tight (0.5-1.0) — downgrade to LEAN
        return TierVerdict('LEAN', resolver_direction,
            f'resolver STRONG but Panel margin {panel_implied_margin:+.1f} mild — downgrade to LEAN',
            None, 0, 0.57)

    # LEAN: resolver LEAN passes through if Panel agrees in direction
    if resolver_tier == 'LEAN':
        return TierVerdict('LEAN', resolver_direction,
            f'resolver LEAN + Panel direction match',
            None, 0, 0.55)

    return TierVerdict('SKIP', None,
        f'unhandled resolver tier {resolver_tier}', None, 0, None)


if __name__ == '__main__':
    # Self-test against today's slate examples
    cases = [
        # PRIME OVER expected (LAD/MIN yesterday gap ~2.6)
        dict(line=9.5, proj_total=9.9, v4_total=11.53, jerry_total=14.9),
        # ELITE UNDER expected (rare big gap below line)
        dict(line=11.5, proj_total=8.0, v4_total=8.5, jerry_total=8.0),
        # SKIP — mild magnitude
        dict(line=8.5, proj_total=8.7, v4_total=8.9, jerry_total=8.6),
        # SKIP — middle UNDER loser band
        dict(line=10.0, proj_total=8.5, v4_total=8.3, jerry_total=8.0),
    ]
    for c in cases:
        v = evaluate_total(**c)
        print(f'line={c["line"]:>5} proj={c["proj_total"]:>5} v4={c["v4_total"]:>5} jerry={c["jerry_total"]:>5}')
        print(f'  -> {v.tier} {v.direction or ""} | {v.reason} | hist {v.historical_hit_rate}')
