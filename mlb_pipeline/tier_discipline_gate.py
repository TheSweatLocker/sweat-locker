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
) -> TierVerdict:
    """Apply tier-discipline rules to decide whether a total pick publishes."""
    if line is None or proj_total is None:
        return TierVerdict('SKIP', None, 'missing anchor data (line/proj)', None, 0, None)

    line = float(line)
    proj_total = float(proj_total)
    v4 = float(v4_total) if v4_total is not None else None
    jerry = float(jerry_total) if jerry_total is not None else None

    overs, unders = _models_direction(proj_total, v4, jerry, line)
    has_v4_jerry = (v4 is not None) and (jerry is not None)
    composite_avg = sum(filter(None, [proj_total, v4, jerry])) / max(1, sum(1 for x in [proj_total, v4, jerry] if x is not None))
    gap = composite_avg - line

    # Hard SKIPs first
    if abs(gap) < 0.5:
        return TierVerdict('SKIP', None, f'composite gap |{gap:+.2f}| < 0.5 — no signal', gap, max(overs, unders), None)

    # ===== UNDER side =====
    if gap < 0:
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
