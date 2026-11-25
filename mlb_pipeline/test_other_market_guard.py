"""other_market_directive: a read must not sell a market its badge is not.

Context (2026-11-24). Andy: "ensure this stops fucking happening."

54 of 3,964 published reads carried a side-market badge over prose that
ASSERTS a total as the bet -- NCAAB 52 of 564 side-market reads (9.2%), MLB
2 of 379 (0.5%), and zero in NCAAF, NBA, NHL and NFL. A badge reading
'VMI ML' sat above prose closing "Under 144.5. Edge lives there."

prose_is_stale() has caught that exact shape since 2026-09-23 and is
correct. It had only ever been handed `short_read` -- and short_read is a
truncated PREFIX of long_read on 99.9% of NCAAB reads, while the offending
sentence sat in the closing 200 characters of long_read on 66 of 68
candidates. The guard was reading the one part of the prose the
contradiction never appears in.

These tests pin the two things a future edit must not quietly undo:

  1. WHAT FIRES. Only an ASSERTED directive ("Under 150.5 is the play") or
     a BARE one ("Under 144.5"). All 54 production hits were read by eye
     before the guard shipped, and the shapes below are taken verbatim from
     them.

  2. WHAT MUST NOT FIRE. Prose that merely ARGUES toward the total is good
     analysis and the commonest shape in the table; a hedged second lean is
     deliberate writing; and a TOTAL badge is free to say "Under 144.5",
     because that is the bet it sells. A detector that fires on the
     by-design case gets muted.

Pure functions, no table reads, no secrets. A pure-function test handed
production credentials fails on a runner that has none -- that is the
"exit 1 after 0s" import crash that broke every daily_card run for a day.
[[feedback_exit_1_after_0s_is_an_import_crash]] ·
[[feedback_a_detector_must_know_the_intentional_case]] ·
[[feedback_guard_belongs_at_the_chokepoint]]
"""
import os

# Placeholders so the module imports anywhere, and so an accidental request
# hits a dead host rather than the real database. setdefault, so a real
# environment still wins.
os.environ.setdefault('SUPABASE_URL', 'http://127.0.0.1:9')
os.environ.setdefault('SUPABASE_KEY', 'test-placeholder')

from jerry_reads_dual_write import (                     # noqa: E402
    other_market_directive, strip_other_market_directive)


# ── 1 · what must FIRE ──────────────────────────────────────────────
def test_bare_directive_fires():
    """The VMI ML read, verbatim from id=27938."""
    prose = ('So the model likes them by 2.6 (against the -5.5 spread), but '
             'the total compression is the real signal. Under 144.5. Edge '
             'lives there.')
    hit = other_market_directive(prose, 'ml')
    assert hit, 'a bare total directive under an ML badge must fire'
    assert hit[0] == 'BARE', hit[0]
    assert '144.5' in hit[1], hit[1]


def test_asserted_directive_fires():
    """id=28474, 'Under 169.5 is the read, Sweat Score 78.'"""
    prose = ('Both teams play deliberate pace. Under 169.5 is the read, '
             'Sweat Score 78. Edge lives there.')
    hit = other_market_directive(prose, 'ml')
    assert hit and hit[0] == 'ASSERTED', hit


def test_fires_on_a_spread_badge_too():
    """id=27651 carried 'Maryland +4.5', not a moneyline."""
    prose = 'Pace and turnovers compress scoring. Under 145.5 is the edge.'
    assert other_market_directive(prose, 'rl')
    assert other_market_directive(prose, 'spread')
    assert other_market_directive(prose, 'puckline')


def test_fires_when_the_directive_carries_a_sweat_score():
    """id=28464, 'Under 149.5 carries a Sweat Score of 86.'"""
    prose = ('Tempo sits at a modest 66.2, which keeps this grind-heavy. '
             'Under 149.5 carries a Sweat Score of 86.')
    assert other_market_directive(prose, 'ml')


def test_fires_on_a_pipe_delimited_footer():
    """id=28484 ended its summary line '| Under 148.5'."""
    assert other_market_directive(
        'LSU by 6 on efficiency. | Under 148.5', 'ml')


# ── 2 · what must NOT fire ──────────────────────────────────────────
def test_arguing_toward_the_total_does_not_fire():
    """The commonest legitimate shape in the table. Must stay silent."""
    for prose in (
        'Pace compresses this toward the under 144.5 over a full game.',
        'Both defenses push this below the market total of 150.5.',
        "The model sees 143.8; the market opened at 148.5.",
        'Turnover rate and slower pace both compress scoring volume.',
    ):
        assert other_market_directive(prose, 'ml') is None, prose


def test_a_hedged_second_lean_does_not_fire():
    for prose in (
        'Baylor covers. Under 163.5 is the secondary lean here.',
        'If you want the total, Under 155.5 is the alternative.',
    ):
        assert other_market_directive(prose, 'ml') is None, prose


def test_a_total_badge_may_sell_its_own_total():
    """A total pick saying 'Under 144.5 is the play' is the bet it sells."""
    prose = 'Pace is dead. Under 144.5 is the play. Edge lives there.'
    for mkt in ('total', 'nrfi', 'yrfi'):
        assert other_market_directive(prose, mkt) is None, mkt


def test_missing_or_unknown_market_is_silent():
    prose = 'Under 144.5.'
    for mkt in (None, '', 'pass', 'fight'):
        assert other_market_directive(prose, mkt) is None, repr(mkt)
    assert other_market_directive(None, 'ml') is None
    assert other_market_directive('', 'ml') is None


def test_a_decimal_is_not_a_sentence_boundary():
    """The first version split on every '.', so '68.5' broke the sentence
    and every match it reported was unreadable. A number mid-sentence must
    not become a bare directive."""
    prose = ('Tempo sits at 68.5 and the away team turns it over on 18.4% of '
             'possessions, which drags the under 149.5 closer to reach.')
    assert other_market_directive(prose, 'ml') is None


# ── 3 · the repair ──────────────────────────────────────────────────
def test_strip_removes_the_sentence_and_keeps_the_analysis():
    prose = ('VMI controls the paint and the model likes them by 2.6. '
             'Under 144.5. Edge lives there.')
    out, removed = strip_other_market_directive(prose, 'ml')
    assert removed == ['Under 144.5.'], removed
    assert 'VMI controls the paint' in out
    assert 'Edge lives there.' in out
    assert '144.5' not in out
    assert other_market_directive(out, 'ml') is None, 'strip must be idempotent'


def test_strip_sweeps_a_dangling_delimiter():
    out, removed = strip_other_market_directive(
        'LSU by 6 on efficiency. | Under 148.5', 'ml')
    assert removed
    assert not out.rstrip().endswith('|'), repr(out)


def test_strip_is_a_no_op_when_nothing_contradicts():
    prose = 'Pace compresses this toward the under over a full game.'
    out, removed = strip_other_market_directive(prose, 'ml')
    assert removed == []
    assert out == prose


def test_strip_never_returns_an_empty_string():
    """A read whose entire prose was the directive must come back None, not
    '', so the caller's `or` fallbacks fire instead of publishing a blank."""
    out, removed = strip_other_market_directive('Under 144.5.', 'ml')
    assert removed
    assert out is None, repr(out)


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
