"""The one correct way to ask "which side is the PUBLIC on?".

══ 2026-11-06 · WHY THIS MODULE EXISTS ══════════════════════════════════
`public_splits_archive.pick_side` carries TWO conventions in one column:
it is the **public** side on oddscrowd (`oc_*`) rows and the **sharp** side
on the `fr_*`, `ftp_*` and `cz_*` rows. Three of four feeds index the sharp
side in a table named *public*
([[feedback_one_column_two_side_conventions]]).

That single ambiguity produced two contradictory studies of the same
question, five days apart:

  * 2026-11-02, reading `pick_side` from the archive: fading the public on
    spreads looked monotonically profitable, +19..+35% ROI by band.
  * 2026-11-04, reading `public_splits_v2`: 50.30% on n=3,883, and WORSE
    as the public share rose.

Resolved today, and the 11-02 result was the artefact. Measured on
`public_splits_v2`, whose `side` column is an explicit HOME/AWAY/OVER/UNDER
and whose two sides sum to 100 in 99%+ of two-sided groups, with the
**public side derived from the ticket numbers rather than any label**:

    spread fade   n=2,751   50.16%   selection vs a side-matched
                                     blanket control: +0.01pp (+0.01 SE)

Zero. The fade on spreads is exactly the blanket base rate of whichever
side it happened to take. It has no selection value at all, and the band
structure runs the wrong way (50-60% public +2.80pp, 80-100% −3.74pp).

So: never read a side from `pick_side` again. Derive it from the numbers.
A label can be wrong in a way that inverts the answer and still looks
well-formed ([[feedback_magnitude_cannot_express_a_side]],
[[feedback_check_which_side_a_column_indexes]]).
"""
from __future__ import annotations

from typing import Optional

#: `pick_side` means the PUBLIC side only on these sources. On every other
#: source it is the SHARP side, i.e. the OPPOSITE of the public one. Kept
#: here so the fact is written down once, but prefer `public_from_pair`:
#: a derivation from two numbers cannot be inverted by a convention.
PICK_SIDE_IS_PUBLIC = {'oc', 'oddscrowd'}

FLIP = {'HOME': 'AWAY', 'AWAY': 'HOME', 'OVER': 'UNDER', 'UNDER': 'OVER'}

#: A pair of shares must land near 100 to be a share pair at all. Feeds
#: round each side independently, so exact 100 is not required -- measured
#: today, genuine pairs land 97..103 and anything outside is a different
#: quantity (a divergence, a strength score) that must not be read as one.
_SHARE_SUM_LO, _SHARE_SUM_HI = 95.0, 105.0


def public_from_pair(shares: dict) -> Optional[tuple]:
    """(public_side, public_share_pct) from {side: ticket_pct}, or None.

    This is the preferred entry point. It reads only the numbers, so no
    source convention can invert it. Accepts a one-sided dict and infers
    the other side as 100 - value, because several feeds store one row.
    """
    if not shares:
        return None
    s = {str(k).upper(): v for k, v in shares.items() if v is not None}
    if len(s) == 1:
        side, val = next(iter(s.items()))
        other = FLIP.get(side)
        if other is None:
            return None
        try:
            s = {side: float(val), other: 100.0 - float(val)}
        except (TypeError, ValueError):
            return None
    if len(s) != 2:
        return None
    try:
        s = {k: float(v) for k, v in s.items()}
    except (TypeError, ValueError):
        return None
    total = sum(s.values())
    if not (_SHARE_SUM_LO <= total <= _SHARE_SUM_HI):
        return None
    pub = max(s, key=lambda z: s[z])
    return pub, 100.0 * s[pub] / total


def public_from_pick_side(pick_side: str, source: str) -> Optional[str]:
    """The PUBLIC side for an archive row, correcting for its source.

    Only for `public_splits_archive`, which has no side-per-source column.
    Prefer `public_from_pair` wherever two shares are available: this
    function is only as right as the convention table above, and that
    table is exactly what went wrong.
    """
    side = str(pick_side or '').upper()
    if side not in FLIP:
        return None
    src = str(source or '').lower()
    if src in PICK_SIDE_IS_PUBLIC:
        return side
    # every other feed stores the SHARP side, so the public is the other one
    return FLIP[side]


def fade_side(public: str) -> Optional[str]:
    """The side a fade takes, given the public side."""
    return FLIP.get(str(public or '').upper())
