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

# ══ 2026-10-03 · THE NEGATIVE-EDGE CAP COULD NOT TOUCH A LEAN PICK ══
# Andy: "if its a known leak we need to fix, if we know the system is bias
# we need to fix not just continue doing what we're doing."
#
# The leak was this: with NEG_EDGE_TIER_CAP = 'LEAN', any edge between
# -5.0pp and +2.0pp "capped" to LEAN — and the picks landing there were
# ALREADY LEAN, so cap_tier_for_edge hit its own
#     if _rank[cap] >= _rank[t]: return t, None
# guard and did nothing. Minnesota Wild at -182 with conviction 62 against
# 64.5% implied is a -2.5pp pick, and it shipped at LEAN onto The Sharp at
# 0.5u. Same shape as feedback_tier_demotion_needs_stake_boundary: a
# demotion that lands on the tier you already hold is not a demotion.
#
# A tier asserts an edge over the market. A NON-POSITIVE edge asserts none
# — our own number says the price is better than our opinion of it — so it
# cannot support a bettable tier. That is the same reasoning
# apply_unpriced_market_gate uses for a pick with no market at all, and it
# does not depend on sample size: it is an internal contradiction, not a
# hit-rate claim. (The NHL edge buckets are n=6-9 and do not even order
# monotonically, so no gate here is justified by them.)
#
# Still a DEMOTION, not a suppression, per the directive above: COVERAGE
# keeps the pick visible on the game card with the edge stated honestly, it
# just stops the card surfaces treating it as a play.
#
# Measured blast radius on live forward picks: 16 — NHL 6, NFL 8, NBA 2;
# NCAAF and MLB zero, their moneylines already carry positive edge.
NO_EDGE_PP = 0.0
NO_EDGE_TIER_CAP = 'COVERAGE'


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


# ── 2026-09-27 · THE MONEYLINE COLUMN IS NAMED THREE DIFFERENT WAYS ──
#
# Found while wiring the edge gate across every sport. The context tables
# do not agree on what the closing moneyline is called:
#
#     MLB    home_ml_close / away_ml_close   (also home_ml_odds)
#     NBA    home_ml_close / away_ml_close
#     NFL    close_home_ml / close_away_ml   (also home_ml_close, _odds)
#     NCAAF  close_home_ml / close_away_ml
#     NHL    BOTH spellings as of today
#
# A single hard-coded lookup therefore works for two sports and silently
# finds nothing for the others — which is precisely the bug that left NHL
# with 0/59 prices and a board of identical home picks, except there the
# writer and reader disagreed inside ONE file.
#
# Resolved in one place with an ordered candidate list. Canonical spelling
# first so the preferred name wins where both exist.
_ML_KEYS = {
    'HOME': ('close_home_ml', 'home_ml_close', 'home_ml_odds', 'home_ml'),
    'AWAY': ('close_away_ml', 'away_ml_close', 'away_ml_odds', 'away_ml'),
}


def ml_price(ctx: Optional[dict], side: str):
    """Closing moneyline for `side` from a context row, whatever it's called."""
    if not isinstance(ctx, dict):
        return None
    for k in _ML_KEYS.get(str(side or '').upper(), ()):
        v = ctx.get(k)
        if v is not None:
            return v
    return None


def apply_to_pick(pp: Optional[dict], ctx: Optional[dict]) -> Optional[dict]:
    """Apply the edge check to a finished primary_play, in place. Returns it.

    ── WHY THIS EXISTS SEPARATELY FROM THE LR PATH ──
    The edge gate first shipped inside defensive_gates' LR override, which
    covers the `lr_v1` engine only. Measured 2026-09-27 over 319 past
    picks, `ensemble_v2` produced 263 of them — so 82% of the board was
    bypassing the gate entirely.

    It showed up immediately on NHL's 9/29 opener: four LR picks carried a
    computed edge and two were correctly capped, while Toronto ML came from
    ensemble_v2 with _model_edge_pp = None and no check at all.

    Five sport files stamp `_engine: 'ensemble_v2'` (game_context,
    nfl_/ncaaf_/nba_/nhl_game_context). Each calls THIS function rather
    than repeating the logic, because five copies of a rule is how the
    three name-matching implementations and the two publish-lock
    mechanisms happened.

    Mutates and returns pp so a call site can wrap its assignment in one
    line. Never raises: a pick must not be lost because its price was
    missing or malformed.
    """
    if not isinstance(pp, dict) or not isinstance(ctx, dict):
        return pp
    try:
        if str(pp.get('type') or '').lower() != 'ml':
            return pp
        side = str(pp.get('side') or '').upper()
        if side not in ('HOME', 'AWAY'):
            return pp
        price = ml_price(ctx, side)
        conv = pp.get('conviction')
        e = edge_pp(conv, price, 'ml')
        if e is None:
            return pp
        pp['_model_edge_pp'] = e
        pp['_ml_price_at_pick'] = price
        tier_after, reason = cap_tier_for_edge(pp.get('tier'), conv, price, 'ml')
        if reason:
            pp['_edge_cap'] = {'from': pp.get('tier'), 'to': tier_after,
                               'reason': reason}
            pp['tier'] = tier_after
        # State the edge in the sub-line the user reads. Appended rather
        # than replacing, because the ensemble sub already names the
        # signals that drove the pick and that is the more useful half.
        phrase = edge_phrase(conv, price, 'ml')
        if phrase:
            sub = str(pp.get('sub') or '')
            if 'implied' not in sub:
                pp['sub'] = f'{sub} · {phrase}' if sub else phrase
    except Exception:
        pass
    return pp


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
    if e <= NO_EDGE_PP:
        # Non-positive edge: our own number does not beat the price. No
        # bettable tier can be supported. See NO_EDGE_PP.
        cap = NO_EDGE_TIER_CAP
    elif e < EDGE_MIN_PP:
        # Positive but thin — real edge, not enough to claim confidence.
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
