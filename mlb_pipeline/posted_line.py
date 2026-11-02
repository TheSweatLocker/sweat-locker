"""Consensus across books that is still a number a book POSTED.

══ WHY THIS EXISTS ══════════════════════════════════════════════════════
A mean of book lines is not a line. Average 146.5 and 147 and you get
146.75, which no book posted, so a pick stored at that number records a bet
nobody could have placed and a grade nobody could have earned. It is the
rule in feedback_consensus_line_must_be_posted -- "a fake line makes a fake
price" -- and project_jerry_published_fake_lines_1019, where 48% of
game_read spreads were numbers nobody posted.

MEASURED 2026-11-02, every context table, every market column, asking only
"is this a multiple of 0.5":

    sport   column          n     not a half-point
    NFL     all four      ~316        0
    NBA     all four       ~114        0
    NHL     open_total      230        4
    NCAAF   close_total     718        9
    NCAAB   close_spread     75       33
    NCAAB   close_total      75       46   <-- 61%

NFL and NBA already store postable numbers. NCAAB does not, because both of
its writers average:

    ncaab_game_context._avg_market_line   sum(vals) / len(vals)
    ncaab_odds_pull._consensus_spread     round(sum(vals) / len(vals), 2)
    ncaab_odds_pull._consensus_total      round(sum(vals) / len(vals), 1)

That is the source of the receipts voided on 11-02 carrying
'Under 157.83333333333334' and 'Under 149.25'. I attributed those to
projected_total at the time. The measurement says close_total alone holds
133.1, 133.9, 135.7 -- the averaged "market" column is enough to produce
them on its own.

══ WHAT THIS RETURNS ══
The MODE: the line the most books posted. Never a computed midpoint. On a
tie it takes the tied value nearest the median of all quotes, and if still
tied the one smaller in absolute value -- deterministic, and the tie-break
can only ever pick a value that was actually quoted.

It does NOT silently round a quote. If every book posts a quarter-point
line, the mode is that quarter-point line and it is returned as-is: the job
here is to stop inventing numbers, not to impose a house convention on a
real one.

    python posted_line.py --self-test
"""
from __future__ import annotations

import statistics
import sys
from collections import Counter
from typing import Iterable, Optional


def _nums(vals: Iterable) -> list[float]:
    out = []
    for v in vals or ():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == f:                      # drop NaN
            out.append(f)
    return out


def posted_consensus(vals: Iterable) -> Optional[float]:
    """The handicap/total the most books posted, or None.

    Ties break toward the median, then toward the smaller magnitude. Both
    tie-breaks can only select a value that appears in `vals`.
    """
    nums = _nums(vals)
    if not nums:
        return None
    counts = Counter(nums)
    top = max(counts.values())
    tied = sorted(v for v, c in counts.items() if c == top)
    if len(tied) == 1:
        return tied[0]
    med = statistics.median(nums)
    return min(tied, key=lambda v: (abs(v - med), abs(v), v))


def posted_price(vals: Iterable) -> Optional[int]:
    """The American price to quote, as an actually-quoted price.

    Prices move continuously so a mean is less obviously fake than an
    averaged LINE, but it is still a price no book showed, and a fake price
    is what inflates every ROI figure computed from it. So: take the quoted
    price nearest the median of the quotes. Returns an int because that is
    what the context columns store.
    """
    nums = _nums(vals)
    if not nums:
        return None
    med = statistics.median(nums)
    best = min(nums, key=lambda v: (abs(v - med), v))
    return int(round(best))


def is_postable(v, *, step: float = 0.5) -> Optional[bool]:
    """True when `v` lands on a `step` boundary. None when not a number.

    The check receipt_invariants applies at the publish chokepoint, exposed
    here so a writer can assert before storing rather than after.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    q = f / step
    return abs(q - round(q)) <= 1e-6


def _self_test() -> int:
    ok = True
    cases = [
        # (quotes, expected)
        ([146.5, 147.0, 146.5], 146.5),        # plain mode; mean is 146.67
        ([146.5, 147.0], 146.5),               # even split -> nearer median,
                                               # then smaller magnitude
        ([-5.5, -5.5, -6.0, -6.0, -5.5], -5.5),
        ([157.5], 157.5),
        ([], None),
        ([None, 'x'], None),
        ([None, 148.5, 'x'], 148.5),
        # the shape that produced the voided receipts: six books, three
        # distinct lines, mean 157.833 which NO book posted
        ([157.5, 157.5, 158.0, 158.0, 158.0, 158.5], 158.0),
        # a genuine quarter-point board is returned as-is, not rounded
        ([2.25, 2.25, 2.75], 2.25),
        # tie between equidistant values -> smaller magnitude
        ([-3.0, -3.0, 3.0, 3.0], -3.0),
    ]
    print('posted_consensus():')
    for vals, want in cases:
        got = posted_consensus(vals)
        good = got == want
        ok = ok and good
        mean = (sum(_nums(vals)) / len(_nums(vals))) if _nums(vals) else None
        print(f'  {"ok  " if good else "FAIL"} {str(vals):52s} -> {got!r:8s} '
              f'(wanted {want!r}; the MEAN would be '
              f'{round(mean, 4) if mean is not None else None})'
              .replace("'", '"') if False else
              f'  {"ok  " if good else "FAIL"} {str(vals)[:50]:50s} -> '
              f'{str(got):8s} wanted {want}  (mean would be '
              f'{round(mean, 4) if mean is not None else None})')

    print('\nposted_price():')
    pcases = [
        ([-110, -110, -115], -110),
        ([-176, -180, -170], -176),
        ([], None),
        ([150], 150),
        # mean is -121.67 which nobody posted; median-nearest quote is -120
        ([-110, -120, -135], -120),
    ]
    for vals, want in pcases:
        got = posted_price(vals)
        good = got == want
        ok = ok and good
        mean = (int(sum(vals) / len(vals))) if vals else None
        print(f'  {"ok  " if good else "FAIL"} {str(vals):26s} -> {str(got):6s} '
              f'wanted {want}  (the old mean would be {mean})')

    print('\nis_postable():')
    icases = [(157.5, True), (158.0, True), (157.8333, False), (149.25, False),
              (-5.5, True), ('x', None), (None, None), (0.0, True)]
    for v, want in icases:
        got = is_postable(v)
        good = got == want
        ok = ok and good
        print(f'  {"ok  " if good else "FAIL"} {str(v):12s} -> {str(got):6s} '
              f'wanted {want}')

    print('\nself-test', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        raise SystemExit(_self_test())
    print(__doc__)
