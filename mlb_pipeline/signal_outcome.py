"""One definition of "was this signal right", for every attribution consumer.

== WHY THIS MODULE EXISTS (2026-10-28) ======================================
`signal_attribution.result` is the PICK's outcome, stamped onto every signal
row that fired on that pick. The grader says so plainly:

    "Each signal snapshot inherits the pick_side result -- so LR_SHADOW
     kind=ok gets marked W when the pick hits, L when it misses."

That is the right thing to store. It is the wrong thing to average per
signal, and the two consumers disagreed about it:

  * refit_signal_registry does invert: `(kind=='ok' and won) or
    (kind=='warn' and not won)`. Correct in spirit.
  * weekly_signal_attribution_report does NOT invert. Its docstring says
    "`kind` is ... ~97% 'ok' by construction". Measured on 4,002 graded rows:
    ok 78.1%, warn 11.6%, neutral 10.2%. So the report's per-signal rates
    were wrong on the 466 warn rows and additionally counted the 409 neutral
    rows it should have excluded -- 875 of 4,002, 21.9%.

And `kind` is not a clean proxy for either consumer:

    kind=ok, sides DIFFER      223 of 3,127   (139 TRUE opposites marked ok,
                                               54 signal_side NEUTRAL,
                                               30 a side signal on a TOTAL pick)
    kind=warn, sides DIFFER    466 of   466   (all true opposites)

So trusting `kind` alone mis-tallies 223 ok rows. The bias has a direction
that matters: both errors penalise a signal for DISAGREEING with the engine,
which is exactly the dissenting/fade voice we most want to measure honestly.

WHAT THIS DOES INSTEAD
Grades `signal_side` against the stored outcome directly -- margin, close
line and total are all on the row -- and returns None rather than a guess
when the comparison is not meaningful:

  * signal_side NEUTRAL                  -> None (it argued nothing)
  * a side signal on a total pick, or a   -> None (different axis; the pick's
    total signal on a side pick              market cannot settle it)
  * no margin / no close line / push      -> None
  * kind='neutral'                        -> None (informational by design)

Falls back to the kind-inversion rule only when the numbers are missing, so
nothing that was countable before stops being counted.

THE SIGN CONVENTION IS PER SPORT, MEASURED, NOT ASSUMED. Correlating the
stored close number against realised home margin, 2026-10-28:

    NBA -0.517   NHL -0.172   MLB -0.133   NCAAB -0.644   NCAAF -0.721
    NFL +0.425   <- the only sport storing a POSITIVE number for a home fav

Getting that backwards labels every favourite a dog, which is why a sport
with no measured convention returns None here rather than a guess.
"""
from __future__ import annotations

from typing import Optional

#: +1.0 when a POSITIVE stored close number means HOME is favoured.
CLOSE_HOME_FAV_SIGN = {'NFL': 1.0, 'NCAAF': -1.0, 'NBA': -1.0,
                       'NHL': -1.0, 'MLB': -1.0, 'NCAAB': -1.0}
SIDES = ('HOME', 'AWAY')
TOTALS = ('OVER', 'UNDER')


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _norm(v) -> str:
    return str(v or '').strip().upper()


def signal_was_right(row: dict, sport: Optional[str] = None) -> Optional[bool]:
    """True / False / None for one signal_attribution row.

    None means "not countable" and must be skipped, not defaulted. A signal
    with no opinion on this market has not been proven wrong.
    """
    kind = str(row.get('kind') or '').lower()
    if kind == 'neutral':
        return None

    sig = _norm(row.get('signal_side'))
    if sig in ('', 'NEUTRAL', 'NONE', 'PASS'):
        return None

    market = str(row.get('pick_market') or '').lower()
    if market in ('pl', 'puckline', 'spread'):
        market = 'rl'
    pick = _norm(row.get('pick_side'))

    # Different axis: the pick's market cannot settle a claim about the other.
    if (sig in SIDES and pick in TOTALS) or (sig in TOTALS and pick in SIDES):
        return None

    sp = _norm(sport or row.get('sport'))
    margin = _f(row.get('actual_margin'))

    if sig in SIDES and market == 'ml':
        if margin is None or margin == 0:
            return _fallback(row, kind)
        return (margin > 0) == (sig == 'HOME')

    if sig in SIDES and market == 'rl':
        ln = _f(row.get('close_line'))
        sgn = CLOSE_HOME_FAV_SIGN.get(sp)
        if margin is None or ln is None or sgn is None:
            return _fallback(row, kind)
        # Normalise to a HOME HANDICAP: negative = home favoured.
        handicap = ln * sgn * -1.0
        adj = margin + handicap
        if adj == 0:
            return None                      # push
        return (adj > 0) == (sig == 'HOME')

    if sig in TOTALS and market == 'total':
        # ══ 2026-10-28 · THERE IS NO CLOSING TOTAL ON THESE ROWS ══
        # The first version of this function graded a total signal as
        # (actual_total > close_line) == (sig == 'OVER'). close_line is the
        # SPREAD on every row, including total-market rows -- measured:
        #
        #   sport market    close_line median   actual_total median
        #   MLB   total               1.5               9.0
        #   NHL   total              -1.5               5.0
        #   NCAAF total             -10.5              45.0
        #   NFL   total               2.5              40.0
        #
        # A 1.5 "total" in baseball is a run line. So that comparison was
        # actual_total against a spread, and it produced a confident, wrong
        # number -- MLB LR_TOTAL_SHADOW came out 36.4% where the kind rule
        # says 53.4%. I nearly reported it.
        #
        # So totals are NOT gradeable from an attribution row as stored, and
        # this returns the kind-inversion fallback rather than a number built
        # from the wrong column. signal_attribution needs its own
        # close_total before a total signal can be side-graded (B229).
        return _fallback(row, kind)

    return _fallback(row, kind)


def _fallback(row: dict, kind: str) -> Optional[bool]:
    """The kind-inversion rule, for rows whose numbers are missing.

    Only `warn` is a trustworthy inversion: all 466 warn rows are true
    opposites. `ok` is 93% agreement, so it is used but is the weaker half of
    this function and the reason the numeric path above is preferred.
    """
    res = str(row.get('result') or '').upper()
    if res not in ('W', 'L'):
        return None
    won = res == 'W'
    if kind == 'warn':
        return not won
    if kind == 'ok':
        return won
    return None


def tally(rows, sport: Optional[str] = None) -> tuple:
    """(n, wins, skipped) over an iterable of attribution rows."""
    n = w = skipped = 0
    for r in rows:
        v = signal_was_right(r, sport)
        if v is None:
            skipped += 1
            continue
        n += 1
        if v:
            w += 1
    return n, w, skipped
