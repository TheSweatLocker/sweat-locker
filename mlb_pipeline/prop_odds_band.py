"""Prop odds band — single source of truth, enforced at GENERATION.

ANDY, 2026-11-10: "For NHL i dont wnat to see +1100 or -4000 props, hell
for any sports thats pointless."

CLAUDE.md has carried the band for weeks -- props -300..+150 -- and until
now it existed only as a constant inside apply_odds_band_gate.py, which:

  * runs in mlb_pipeline.yml ONLY, so no other sport's pipeline calls it,
  * gates on FLOORED_FAMILIES, a list of MLB families (hits_over,
    hits_under, bb_under, hr_under), so even when it iterates the NHL and
    NBA tables no row of theirs is ever eligible,
  * and only looks at rows already in a publishable tier, while every NHL
    row ever written sits at COVERAGE.

Three independent reasons the same gate could never touch an NHL prop. The
result Andy saw: an NHL `goals_under 0.5` quoting -4000 on the under and
+1000 on the over, which is a bet that risks 40 units to win 1, or pays 10
on a skater scoring. Live tonight at generation.

WHY A PURE MODULE AND NOT AN IMPORT OF THAT SCRIPT
--------------------------------------------------
apply_odds_band_gate reads .env and does `os.environ['SUPABASE_KEY']` at
module scope, so importing it from a generator couples row construction to
credentials being present. This file has no imports beyond typing and no
side effects, the same shape as prop_ban_policy -- it is policy, not a job.

apply_odds_band_gate now imports backed_odds from here rather than keeping
its own copy. A hand-kept second copy of a lookup the real code owns drifts
the moment one of them changes
(feedback_a_blocklist_duplicating_a_lookup_table), and this one decides
whether a row is bettable at all.

WHAT THIS DOES NOT CHANGE
-------------------------
The family-scoped -200 floor in apply_odds_band_gate stays exactly as it
is. That floor was measured and is deliberately narrow: a blanket -200
would have thrown away a profitable set (+46.36u on n=699), because
rbis/runs/total_bases unders at -200..-300 are near-certainties that pay
while hits and bb at the same price are the trap. Every family in that
measurement sits INSIDE this band, so the two rules do not overlap or
contradict -- this one removes what cannot be bet profitably at any price,
that one picks between families inside the bettable range.

BAND_HI is +150 and not higher on purpose. A longshot prop is not a small
edge, it is a different product: the NHL population at a median implied
25% is what produced a fake "74.4% hit rate" and a 300% ROI figure, and
the whole table sat unscored behind it.
"""
from __future__ import annotations

#: Worse than BAND_LO or longer than BAND_HI is not a prop we will quote,
#: in any sport, in any family, at any tier. CLAUDE.md: "-300 to +150.
#: Anything outside -> SKIP."
BAND_LO = -300
BAND_HI = 150


def is_price(v) -> bool:
    """True when v looks like an American price rather than an empty column.

    American odds never fall between -100 and +100, so that range is the
    signature of a 0, a null written as a number, or a handicap that landed
    in an odds column (feedback_parse_a_pick_by_shape_not_vocabulary).
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return not (-100 < f < 100)


def backed_odds(row) -> int | None:
    """The price on the side being BACKED, not the prop's nominal over.

    Resolved from the row's own `direction`, because prop_type carries the
    listing and direction carries the side. Returns None when that side was
    not quoted, which is NOT out-of-band -- it is unknown, and an unknown
    price must not read as a refusal (feedback_unmeasured_signal_must_not_
    outweigh_measured).
    """
    if not isinstance(row, dict):
        return None
    d = str(row.get('direction') or '').strip().lower()
    v = row.get('book_over_odds') if d == 'over' else row.get('book_under_odds')
    if v is None or not is_price(v):
        return None
    return int(round(float(v)))


def in_band(odds) -> bool:
    """True when this price is inside the band. None -> True (unknown)."""
    if odds is None:
        return True
    if not is_price(odds):
        return True
    return BAND_LO <= float(odds) <= BAND_HI


def row_in_band(row) -> tuple[bool, str]:
    """(ok, reason) for one prop row, judged on the side it backs.

    A row with no price on its own side passes here and is refused later by
    whatever needs a price to size the bet. This gate answers one question
    only: is the quoted price bettable.
    """
    o = backed_odds(row)
    if o is None:
        return True, 'no price on the backed side - not judged here'
    if o < BAND_LO:
        return False, f'{o:+d} is past the {BAND_LO:+d} juice floor'
    if o > BAND_HI:
        return False, f'{o:+d} is longer than the {BAND_HI:+d} ceiling'
    return True, f'{o:+d} in band'


if __name__ == '__main__':
    # Shapes taken from live rows in the four prop tables on 2026-11-10.
    cases = [
        # (row, expected_ok, label)
        ({'direction': 'under', 'book_over_odds': 1000,
          'book_under_odds': -4000}, False, 'NHL goals_under 0.5 (Artem Zub)'),
        ({'direction': 'over', 'book_over_odds': 1000,
          'book_under_odds': -4000}, False, 'same prop, over side'),
        ({'direction': 'under', 'book_over_odds': 650,
          'book_under_odds': -1600}, False, 'NHL goals_under (Nick Cousins)'),
        ({'direction': 'over', 'book_over_odds': 130,
          'book_under_odds': None}, True, 'NBA reb_over 2.5 at +130'),
        ({'direction': 'under', 'book_over_odds': None,
          'book_under_odds': -245}, True, 'NBA steals_under at -245'),
        ({'direction': 'under', 'book_over_odds': None,
          'book_under_odds': -120}, True, 'NBA pts_under at -120'),
        ({'direction': 'over', 'book_over_odds': -301,
          'book_under_odds': None}, False, 'boundary: -301 refused'),
        ({'direction': 'over', 'book_over_odds': -300,
          'book_under_odds': None}, True, 'boundary: -300 kept'),
        ({'direction': 'over', 'book_over_odds': 150,
          'book_under_odds': None}, True, 'boundary: +150 kept'),
        ({'direction': 'over', 'book_over_odds': 151,
          'book_under_odds': None}, False, 'boundary: +151 refused'),
        ({'direction': 'under', 'book_over_odds': 120,
          'book_under_odds': None}, True, 'under with no under price - unknown'),
        ({'direction': 'over', 'book_over_odds': 0,
          'book_under_odds': None}, True, 'zero is not a price'),
        ({'direction': '', 'book_over_odds': None,
          'book_under_odds': None}, True, 'empty row'),
    ]
    bad = 0
    for row, want, label in cases:
        ok, why = row_in_band(row)
        flag = 'ok ' if ok == want else 'FAIL'
        if ok != want:
            bad += 1
        print(f'  {flag}  {label:44s} -> {"KEEP" if ok else "DROP":4s}  {why}')
    print(f'\n  band {BAND_LO:+d}..{BAND_HI:+d}   '
          f'{len(cases) - bad}/{len(cases)} cases as expected')
    raise SystemExit(1 if bad else 0)
