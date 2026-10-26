"""THE close_spread SIGN CONVENTION, IN ONE PLACE.

══ WHY THIS EXISTS (2026-10-25) ════════════════════════════════════════════
The convention is per-sport and it is retyped in at least eight modules, in
TWO different parameterisations that look contradictory but are not:

    HOME_FAV_SIGN      = {'NCAAF': -1.0, 'NFL': +1.0}   # (cs*s) > 0 => home fav
    HOME_HANDICAP_SIGN = {'NFL':   -1.0, 'NCAAF': +1.0}  # cs*s = home handicap

Both are correct. One is the negation of the other. Having both in circulation
under near-identical names is a trap, and it caught me inside a single
session: having validated NFL=-1/NCAAF=+1 for the handicap form in one script,
I typed the reverse in the next one and measured NCAAF home favourites
covering 2.6% (1-37). That is arithmetically impossible -- spreads are set to
balance near 50% -- and the implausibility is the ONLY reason it was caught.
A sign error that lands on a plausible number does not get caught at all, and
it would sit underneath every weight fitted on top of it.

So: import from here. Do not retype a sign dict.

══ PROVENANCE ══════════════════════════════════════════════════════════════
Measured, not assumed. For each sport, candidate cover formulas were scored
against the STORED result on graded rows, and the winner reproduced it on:

    NFL     56 / 56     (100.0%)
    NCAAF  310 / 310    (100.0%, after excluding 2026-09-12)

2026-09-12 is excluded because `close_line` is stored with an INVERTED sign on
that date only: all 26 NCAAF rows where the formula disagreed with the stored
result fall on it, and the opposite sign explains 26 of 26. If you are
grading NCAAF history, treat that date as suspect.

NHL and NBA are present for completeness and are NOT validated to the same
standard here. Note separately that `projected_spread` is a different column
with its OWN per-sport meaning -- a BETTING spread on NBA (109/109) and a
MARGIN on NHL (202/202) -- so do not reuse these constants for it.
"""
from __future__ import annotations

#: Multiply `close_spread` by this to get the HOME team's handicap.
#: Negative handicap => the home team is laying points (home is favoured).
HOME_HANDICAP_SIGN: dict[str, float] = {
    'NFL': -1.0,       # validated 56/56
    'NCAAF': +1.0,     # validated 310/310 (ex 2026-09-12)
    'NHL': +1.0,       # NOT validated to the same standard
    'NBA': +1.0,       # NOT validated to the same standard
}

#: The other parameterisation, derived rather than retyped, so the two can
#: never drift apart: (close_spread * HOME_FAV_SIGN) > 0 => home favoured.
HOME_FAV_SIGN: dict[str, float] = {
    k: -v for k, v in HOME_HANDICAP_SIGN.items()
}

#: close_spread is stored with an inverted sign on these dates. See docstring.
SUSPECT_LINE_DATES: dict[str, set[str]] = {'NCAAF': {'2026-09-12'}}


class UnknownSport(KeyError):
    """Raised rather than defaulting. A silent default is a sign error."""


def home_handicap(sport: str, close_spread) -> float | None:
    """The HOME team's handicap. Negative means home is laying points."""
    if close_spread is None:
        return None
    s = str(sport or '').upper()
    if s not in HOME_HANDICAP_SIGN:
        raise UnknownSport(
            f'no validated close_spread sign for {s!r}; add it to '
            f'sport_line_sign.py WITH its validation count rather than '
            f'guessing a default')
    try:
        return float(close_spread) * HOME_HANDICAP_SIGN[s]
    except (TypeError, ValueError):
        return None


def is_home_favoured(sport: str, close_spread) -> bool | None:
    h = home_handicap(sport, close_spread)
    if h is None:
        return None
    return h < 0


def covers(sport: str, close_spread, home_margin, side: str):
    """Did `side` cover? True / False / None for a push or missing input.

    `home_margin` is home_score - away_score. This is the one primitive that
    should be used for spread grading, because it needs no caller-side sign
    reasoning at all.
    """
    h = home_handicap(sport, close_spread)
    if h is None or home_margin is None:
        return None
    try:
        m = float(home_margin)
    except (TypeError, ValueError):
        return None
    sd = str(side or '').upper()
    if sd == 'HOME':
        v = m + h
    elif sd == 'AWAY':
        v = -m - h
    else:
        return None
    if abs(v) < 1e-9:
        return None          # push
    return v > 0


def line_is_suspect(sport: str, game_date) -> bool:
    """True when this sport/date is in the known inverted-sign window."""
    return str(game_date) in SUSPECT_LINE_DATES.get(
        str(sport or '').upper(), set())


# The two dicts must stay exact negations of each other.
assert all(HOME_FAV_SIGN[k] == -v for k, v in HOME_HANDICAP_SIGN.items())
