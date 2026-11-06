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


#: a consensus must be this many books, and this share of the books quoting,
#: before it is allowed to overrule the move limit. Three books cannot all
#: publish the same alt line by accident; one book can.
_CONSENSUS_MIN_BOOKS = 3
_CONSENSUS_MIN_SHARE = 0.5


def validate_total(sport: str, new_total, prior_total=None,
                   consensus=None
                   ) -> Tuple[Optional[float], Optional[str]]:
    """-> (total_to_store, rejection_reason_or_None).

    Returns the new total when it is credible. When it is not, returns the
    PRIOR total (which may be None if there isn't one) plus a reason.
    Unknown sports pass through unchanged - a missing rule must never be
    able to silently drop a market.

    `consensus` is the market's own answer, as (line, books_at_line,
    books_quoting). When the incoming total IS that consensus, it is
    accepted whatever the move, and the reason says so.

    ══ 2026-11-05 · WHY THE CONSENSUS ESCAPE HATCH EXISTS ═════════════
    The move rule had no way out, and it is keyed on the value it already
    stored, so ONE rejection froze the column forever: every later pull is
    still >max_move from the kept prior, so it is rejected again for the
    same reason, every run, until the game is played.

    2026-11-05 JAX @ BAL is the proof. BAL lost Tyler Huntley and the total
    went 49.5 -> 39.5, a 9-point move that is real: 101 of 132 quotes
    captured after 20:00 UTC sat at 39.5, and it had been 39.5 at every
    sample from 12:00 on. The guard called it a bad feed and kept 48.5. The
    Sweat Card then published `Over 48.5` at -110 as its RANK 1 pick - a
    line no book posted all day, 8.5 points off the market. Every model
    lens inherited it too (projected_total 49.17, v3 49.17, v4 46.24,
    mc_p_over_line 0.714), while the one lens NOT fed by the column, the
    Panel, sat at 39.4 and was right.

    Blast radius, measured the same night: of 15 forward NFL games, 14 held
    a stored total within 0.5 of the market. The guard fires rarely - and
    when it fires on a real move it never lets go. The spread on that same
    row was correct (-9.5, matching 82 of 132 quotes), because nothing
    guards the spread.

    A bad feed is one book, or one capture. A real move is most of the
    books, and it persists. That is the distinction the rule was missing:
    it compared the new value against YESTERDAY instead of against the
    OTHER BOOKS. ([[feedback_consensus_line_must_be_posted]],
    [[project_nfl_stale_line_and_inverted_weights_1030]])
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
            c_line, c_books, c_total = _consensus(consensus)
            _strong = (c_line is not None
                       and lo <= c_line <= hi
                       and c_books >= _CONSENSUS_MIN_BOOKS
                       and c_books >= _CONSENSUS_MIN_SHARE * max(1, c_total))
            if _strong and abs(c_line - n) < 0.01:
                return n, (f'total moved {n - p:+.1f} ({p} -> {n}), past the '
                           f'{max_move} point {sport} limit, but {c_books} of '
                           f'{c_total} books post {n} - ACCEPTED as a real '
                           f'move, not a bad feed')
            if _strong:
                # The incoming quote is ONE book and it disagrees with its
                # own market, so it is not trusted - but neither is the
                # prior, which the market has left behind. Store what the
                # books actually post. Keeping `p` here is what froze
                # JAX@BAL: the caller's single book said 39.5, the market
                # said 40.0, and requiring an exact match sent us back to a
                # stale 48.5 that NO book posted. The guard exists to stop
                # us storing a number nobody quotes; the consensus is the
                # one number we know is quoted
                # ([[feedback_consensus_line_must_be_posted]]).
                return c_line, (
                    f'total moved {n - p:+.1f} ({p} -> {n}), past the '
                    f'{max_move} point {sport} limit, and the incoming quote '
                    f'is not the market either - stored the CONSENSUS {c_line} '
                    f'({c_books} of {c_total} books) instead of the stale {p}')
            return p, (f'total moved {n - p:+.1f} ({p} -> {n}), beyond the '
                       f'{max_move} point {sport} limit and not corroborated '
                       f'by the book consensus ({c_books} of {c_total} at '
                       f'{c_line}) - almost certainly a bad feed (alt line '
                       f'or wrong game); kept {p}')
    return n, None


def _consensus(consensus) -> Tuple[Optional[float], int, int]:
    """(line, books_at_line, books_quoting) from a caller-supplied tuple."""
    if not consensus:
        return None, 0, 0
    try:
        line, books, quoting = consensus
        return _f(line), int(books or 0), int(quoting or 0)
    except (TypeError, ValueError):
        return None, 0, 0


def consensus_from_outcomes(books_points) -> Tuple[Optional[float], int, int]:
    """Modal line across per-book points -> (line, books_at_line, quoting).

    `books_points` is any iterable of each book's posted number for ONE
    side of ONE market. Ties go to the line nearer the middle of the
    quotes, so an even split cannot invent a number no book posts
    ([[feedback_consensus_line_must_be_posted]]).
    """
    vals = [x for x in (_f(v) for v in (books_points or [])) if x is not None]
    if not vals:
        return None, 0, 0
    counts: dict = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.values())
    tied = sorted(v for v, c in counts.items() if c == best)
    mid = sorted(vals)[len(vals) // 2]
    line = min(tied, key=lambda v: (abs(v - mid), v))
    return line, counts[line], len(vals)


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
