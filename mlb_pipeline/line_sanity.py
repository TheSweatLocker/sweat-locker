"""Reject market lines that cannot be real before they reach a card.

Andy 2026-09-27, on BAL @ DAL:
  "Total moved 51.5 -> 62.5. An 11-point move in an NFL total is almost
   certainly a bad feed, such as an alt line or the wrong game. The UNDER
   call and the 'lenses split 10.9 pts' note both inherit that error."

Confirmed, and the consequence was worse than a wrong number on screen:
every model lens on that game sat at 48-57 (v3 52.59, projected 51.59,
panel 48.49, MC 57.08, SP+ points 51.6) while the "market" said 62.5, so
the card computed midpoint 57.0 < 62.5 and printed UNDER. Against the
OPENING total of 51.5 the same midpoint says OVER. A bad feed did not just
mislabel the line, it inverted the pick.

── WHY MOVEMENT, NOT AN ABSOLUTE BAND ───────────────────────────────────
Measured over 7,550 graded NFL games, close_total runs 28.5 to 63.5 with
p99 at 55.5. So 62.5 is extreme but NOT impossible, and a band tight
enough to catch it would reject real shootout lines.

The movement is unambiguous. Across 316 NFL context rows this season:
    mean |move| 0.39   median 0.00   p95 3.0   max 11.0
An 11-point move is 3.7x the 95th percentile and the largest of the
season. Real NFL totals move a point or two on weather or a QB scratch;
they do not move eleven. So the absolute band stays wide (it only catches
true garbage) and the MOVE does the real work.

This is not one game. The same window contains JAX@DEN 45.5 -> 35.5,
GB@NYJ 44.5 -> 37.5, TEN@NYG 44.5 -> 37.5 and CLE@TB 41.5 -> 48.5.

── KEEP THE LAST GOOD VALUE, AND SAY SO ─────────────────────────────────
On rejection the prior line is retained rather than the row being blanked.
A missing total hides the market from every lens that needs it; a stale
but plausible one is wrong by at most the real drift, which the measured
median puts at zero. The reason is returned so the caller can record it
instead of the substitution happening silently.
"""
from __future__ import annotations

from typing import Optional, Tuple

# (min_plausible, max_plausible, max_single_move). Bands are deliberately
# wider than observed history so they only ever catch true garbage —
# an alt line, a different game, a units mix-up. The move threshold is what
# catches realistic-looking bad feeds.
_TOTAL_RULES = {
    # NFL history: 28.5 .. 63.5 (n=7,550), p99 55.5. Moves p95 3.0.
    'NFL':   (24.0, 70.0, 6.0),
    # NCAAF history: 26 .. 79.5 (n=6,492), p99 70.0. Bigger scale, so a
    # proportionally larger move is still ordinary.
    'NCAAF': (22.0, 90.0, 8.0),
    'NBA':   (160.0, 280.0, 12.0),
    'NCAAB': (95.0, 200.0, 10.0),
    'NHL':   (4.0, 9.0, 1.5),
    'MLB':   (5.0, 15.0, 2.5),
}


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_total(sport: str, new_total, prior_total=None
                   ) -> Tuple[Optional[float], Optional[str]]:
    """-> (total_to_store, rejection_reason_or_None).

    Returns the new total when it is credible. When it is not, returns the
    PRIOR total (which may be None if there isn't one) plus a reason.
    Unknown sports pass through unchanged — a missing rule must never be
    able to silently drop a market.
    """
    n = _f(new_total)
    p = _f(prior_total)
    rule = _TOTAL_RULES.get(str(sport or '').upper())
    if n is None or rule is None:
        return n, None
    lo, hi, max_move = rule

    if not (lo <= n <= hi):
        return p, (f'total {n} outside plausible {sport} band [{lo}, {hi}] '
                   f'- kept {p}')

    if p is not None:
        move = abs(n - p)
        if move > max_move:
            return p, (f'total moved {n - p:+.1f} ({p} -> {n}), beyond the '
                       f'{max_move} point {sport} limit - almost certainly a '
                       f'bad feed (alt line or wrong game); kept {p}')
    return n, None


def total_disagrees_with_models(total, model_totals, tolerance: float = 5.0
                                ) -> Optional[str]:
    """Flag a total that no model lens comes near. Advisory only.

    A second, independent check on the same failure: on BAL @ DAL every
    lens sat within 48-57 while the line read 62.5. This does NOT reject —
    a market legitimately disagreeing with our models is exactly the
    situation an edge is made of, and suppressing it would suppress the
    product. It returns a note so the disagreement can be SHOWN rather
    than quietly averaged into a pick.
    """
    t = _f(total)
    vals = [x for x in (_f(v) for v in (model_totals or [])) if x is not None]
    if t is None or not vals:
        return None
    nearest = min(abs(t - v) for v in vals)
    if nearest <= tolerance:
        return None
    return (f'market total {t} is {nearest:.1f} pts from the nearest model '
            f'lens (range {min(vals):.1f}-{max(vals):.1f})')
