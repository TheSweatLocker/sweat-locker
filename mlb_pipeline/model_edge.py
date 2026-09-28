"""The one place that answers "is there actually an edge on this pick?"

Andy 2026-09-27, on BAL @ DAL:
  "No edge on the pick. BAL at -203 implies about 67%, and the model says
   67%. That's a coin flip against the vig, yet it's labeled a LEAN. The
   card should show edge (model % minus implied %), not raw conviction."

He is right, and the row confirms it exactly: close_away_ml -203 implies
67.0%, and the LR model's p(BAL) is 0.6698 -> 67.0%. Edge 0.0pp, published.

── WHAT WAS ACTUALLY BROKEN ──────────────────────────────────────────────
Not the display. `conviction` on the LR path IS the model's win probability
(defensive_gates.py ~1206: conviction = p * 100), and the tier bands are
ABSOLUTE thresholds applied to that number. Nothing anywhere compares it to
the price. So conviction 59 is treated identically at -110 (a ~6pp edge)
and at -285 (a ~15pp LOSS).

Measured 2026-09-27 over 100 past/today ML picks with closing prices:
    PRIME     n=31  mean model edge  +5.05pp   -EV by >2pp  13%
    STRONG    n=32  mean model edge  -1.77pp   -EV by >2pp  66%   <-- broken
    LEAN      n=14  mean model edge  +3.58pp   -EV by >2pp  36%
    COVERAGE  n=23  mean model edge  -0.86pp   -EV by >2pp  48%
Worst published: Cincinnati ML -285 at conviction 59 (implied 74.0%),
labelled STRONG -- a -15.0pp pick by our own model's number.

A hypothesis worth recording as REJECTED: conviction is NOT simply the
market price restated. Correlation between conviction and implied
probability is -0.170 across those 100 picks. The model does form an
independent opinion; the pipeline just never checks it against the price.

── WHY THIS IS ML-ONLY, AND WHY THAT MATTERS ─────────────────────────────
`conviction` and the moneyline's implied probability describe the SAME
event only for a moneyline pick: this team wins the game. For a spread or
total they describe different events, and subtracting them is meaningless.
An earlier pass of this analysis compared spread picks against moneyline
prices and produced nonsense like "Arizona -33.5 is a -27.5pp edge" --
covering 33.5 points is not the same proposition as winning at -6500.
So: edge_pp() refuses anything that is not a moneyline. A spread/total
edge needs a cover probability, which the pipeline does not currently
produce.

── DEMOTE, DO NOT KILL ───────────────────────────────────────────────────
Andy, separately: "i just dont want to tread the line of engine passing on
everything, every quant lands on a side." Negative model edge therefore
CAPS the tier rather than suppressing the pick. The play still shows, with
an honest label and the edge stated, instead of vanishing.
"""
from __future__ import annotations

from typing import Optional

# A pick needs to clear the vig by this much before its tier is allowed to
# claim confidence. 2.0pp is deliberately modest: it is wide enough to
# absorb the rounding in `conviction` (an integer percent) plus normal
# line shopping, and narrow enough that it does not gut the board. On the
# measured 100-pick sample it leaves PRIME essentially untouched (13% of
# PRIME sits below it) while catching the 66% of STRONG that is underwater.
EDGE_MIN_PP = 2.0

# Tier ceilings applied when the model's own edge is negative. These
# demote; nothing here suppresses a pick.
NEG_EDGE_TIER_CAP = 'LEAN'
BAD_EDGE_TIER_CAP = 'COVERAGE'
BAD_EDGE_PP = -5.0


def implied_prob(american_price) -> Optional[float]:
    """American odds -> implied probability including the vig (0..1).

    -203 -> 0.670      +153 -> 0.395
    Returns None on anything unparseable rather than guessing, so a missing
    price can never silently read as a 50/50.
    """
    try:
        p = float(american_price)
    except (TypeError, ValueError):
        return None
    if p == 0:
        return None
    return abs(p) / (abs(p) + 100.0) if p < 0 else 100.0 / (p + 100.0)


def edge_pp(conviction, american_price, pick_type: str = 'ml') -> Optional[float]:
    """Model edge in percentage points: conviction - implied probability.

    Positive means the model thinks the side wins more often than the price
    requires. Returns None when the comparison is not meaningful:
      * pick_type is not a moneyline (see the module note)
      * conviction or the price is missing/unparseable
    """
    if str(pick_type or '').lower() != 'ml':
        return None
    ip = implied_prob(american_price)
    if ip is None:
        return None
    try:
        conv = float(conviction)
    except (TypeError, ValueError):
        return None
    return conv - (ip * 100.0)


def edge_phrase(conviction, american_price, pick_type: str = 'ml') -> str:
    """Short user-facing clause stating the edge, or '' when not applicable.

    Reads as: "67% vs 67% implied - no edge" / "61% vs 55% implied - +6.0pp edge"
    """
    e = edge_pp(conviction, american_price, pick_type)
    if e is None:
        return ''
    ip = implied_prob(american_price)
    conv = float(conviction)
    if abs(e) < EDGE_MIN_PP:
        return f'{conv:.0f}% vs {ip*100:.0f}% implied — no edge'
    return f'{conv:.0f}% vs {ip*100:.0f}% implied — {e:+.1f}pp edge'


def cap_tier_for_edge(tier: str, conviction, american_price,
                      pick_type: str = 'ml') -> tuple[str, Optional[str]]:
    """-> (tier_after_cap, reason_or_None). Only ever demotes.

    Leaves the tier alone when the edge cannot be computed — an unknown
    edge is not evidence of a bad one, and silently demoting every pick
    with a missing price would be the same class of mistake as treating a
    missing price as a 50/50.
    """
    t = str(tier or '').upper()
    e = edge_pp(conviction, american_price, pick_type)
    if e is None or t not in ('PRIME', 'STRONG', 'LEAN'):
        return t, None
    _rank = {'COVERAGE': 1, 'LEAN': 2, 'STRONG': 3, 'PRIME': 4}
    if e <= BAD_EDGE_PP:
        cap = BAD_EDGE_TIER_CAP
    elif e < EDGE_MIN_PP:
        cap = NEG_EDGE_TIER_CAP
    else:
        return t, None
    if _rank.get(cap, 9) >= _rank.get(t, 0):
        return t, None          # already at or below the cap
    ip = implied_prob(american_price)
    # ASCII only: this string is printed by pipeline steps whose stdout is
    # cp1252 on the Windows runners, where a unicode arrow raises
    # UnicodeEncodeError and takes the step down with it.
    return cap, (f'edge:{e:+.1f}pp (model {float(conviction):.0f}% vs '
                 f'{ip*100:.0f}% implied) -> capped to {cap}')
