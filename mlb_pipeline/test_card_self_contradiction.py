"""card_self_contradiction: what blocks a publish, and who decides a side.

Context (2026-11-22). This check had been CRITICAL continuously since
2026-10-25 and was logged "untouched" on four separate days. The reason was
COVER: it fired on 188 of 375 eligible published spread picks -- half the
card -- and the picks it flagged went 48.6% (n=105) against 51.2% (n=170) for
the ones it passed. SIDE's 61.3%-vs-41.4% win-rate gap turned out to be a
favourite/dog mix (111/13 against 13/15); priced at the close it is -1.03u on
n=124 against -5.70u on n=28, inside one standard deviation. PRICE ran
BACKWARDS: the picks it called mispriced returned -0.9% ROI on n=40 against
-8.2% on n=136.

Buried under those 188 rows were eight published picks that named a side the
closing moneyline priced the other way, including `BAL +8.5` on a team the
book had at -375. Those are what this check is for.

So these tests pin two things a future edit must not quietly undo:
  1. FLIP is the only blocking kind.
  2. "Which side is the favourite" is answered by the MONEYLINE, never by a
     spread column, because a spread column is where the sign-convention bugs
     live -- three separate ones in this check's own history.

Pure-function tests on purpose: the check itself needs six table reads, which
is why its policy went four weeks without one.
[[feedback_no_unit_test_ran_in_ci]] · [[feedback_a_detector_can_be_the_defect]]
"""
import consistency_watchdog as cw


# ── 1. FLIP, and only FLIP, blocks ────────────────────────────────────────
def test_only_flip_blocks():
    assert tuple(cw._CSC_BLOCKING_KINDS) == ('FLIP',)


def test_the_unmeasured_kinds_cannot_block():
    # Each of these was replayed and graded; none separates winners from
    # losers. Re-promoting one needs a new measurement, not a code edit.
    for kind in ('COVER', 'SIDE', 'PRICE', 'STALE', 'BOARD'):
        assert kind not in cw._CSC_BLOCKING_KINDS, kind


# ── 2. the moneyline names the favourite ──────────────────────────────────
def test_ml_favourite_reads_both_column_spellings():
    # football and the rest name these columns the other way round
    assert cw._ml_favourite({'close_home_ml': -375, 'close_away_ml': 295}) == 'HOME'
    assert cw._ml_favourite({'home_ml_close': 295, 'away_ml_close': -375}) == 'AWAY'


def test_ml_favourite_refuses_a_pickem():
    # The two false positives this arbiter produced across 531 published
    # spread picks were both near-pick'em NCAAF games. At -108/-111 the
    # "favourite" is 0.7pp of rounding noise.
    assert cw._ml_favourite({'close_home_ml': -108, 'close_away_ml': -111}) is None
    assert cw._ml_favourite({'close_home_ml': -111, 'close_away_ml': -109}) is None
    # ...and still answers when the book is actually taking a side
    assert cw._ml_favourite({'close_home_ml': -170, 'close_away_ml': 132}) == 'HOME'


def test_ml_favourite_is_silent_without_a_price():
    assert cw._ml_favourite({}) is None
    assert cw._ml_favourite({'close_home_ml': -200, 'close_away_ml': None}) is None
    assert cw._ml_favourite({'close_home_ml': -200, 'close_away_ml': ''}) is None
    # a zero is not a price, and must not read as one
    assert cw._ml_favourite({'close_home_ml': 0, 'close_away_ml': 0}) is None


def test_ml_favourite_reads_no_spread_column():
    # The whole point: hand it a close_spread that contradicts the price and
    # it must still answer from the price alone.
    g = {'close_home_ml': -375, 'close_away_ml': 295, 'close_spread': -99.5}
    assert cw._ml_favourite(g) == 'HOME'


# ── 3. the wrong-side predicate, on the rows that produced it ─────────────
def test_bal_plus_8_5_at_minus_375_is_wrong_sided():
    # 2026-09-20, STRONG. The oc-flip gate rewrote side and label and left
    # the line, with a sport-blind float(close_spread) that NFL stores the
    # other way round. Published "BAL +8.5" on a -375 favourite.
    assert cw._published_wrong_side(8.5, 'HOME', 'HOME') is True


def test_ten_at_ind_is_wrong_sided_after_the_move():
    # 2026-10-18, STRONG. Went out at IND -6.5; TEN closed the -205
    # favourite. Whatever moved it, the number on screen is on the wrong
    # side of the board now.
    assert cw._published_wrong_side(-6.5, 'HOME', 'AWAY') is True


def test_a_correct_dog_pick_is_not_wrong_sided():
    # the control that matters: taking a dog is a bet, not a bug
    assert cw._published_wrong_side(7.0, 'AWAY', 'HOME') is False
    assert cw._published_wrong_side(-8.5, 'AWAY', 'AWAY') is False


def test_no_arbiter_means_no_finding():
    # A detector failing closed here would mean REPORTING the unknown.
    assert cw._published_wrong_side(-3.5, 'HOME', None) is False
    assert cw._published_wrong_side(-3.5, 'HOME', '') is False


def test_an_unparseable_or_zero_line_is_not_a_finding():
    assert cw._published_wrong_side(None, 'HOME', 'AWAY') is False
    assert cw._published_wrong_side('', 'HOME', 'AWAY') is False
    assert cw._published_wrong_side('pick', 'HOME', 'AWAY') is False
    # a pick'em line carries no side to be wrong about
    assert cw._published_wrong_side(0, 'HOME', 'AWAY') is False


def test_a_missing_side_is_not_a_finding():
    assert cw._published_wrong_side(-3.5, '', 'AWAY') is False
    assert cw._published_wrong_side(-3.5, None, 'AWAY') is False


if __name__ == '__main__':
    import sys
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith('test_') and callable(f)]
    bad = 0
    for n, f in fns:
        try:
            f()
            print(f'  ok   {n}')
        except AssertionError as e:                            # noqa: PERF203
            bad += 1
            print(f'  FAIL {n}  {e}')
    print(f'\n{len(fns) - bad}/{len(fns)} passed')
    sys.exit(1 if bad else 0)
