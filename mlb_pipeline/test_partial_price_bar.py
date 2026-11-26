# -*- coding: utf-8 -*-
"""Regression: a signal's OWN partial price bar beats a constant from
another sport.

== WHY THIS EXISTS (2026-11-26) ==========================================
Andy: "change weitghts lets worok it", after the college-basketball sweep
found the engine crediting phantom edge in NCAAB.

THE CHAIN. refit_signal_registry computes, per signal, the mean breakeven
of the closing prices its firings actually took (`be_sum / be_n`). It uses
that as the tier/weight bar ONLY at >=80% price coverage; below that it
fell back to `_MARKET_BE_FALLBACK`, whose ml entry is 58.33%.

That 58.33% was measured on graded receipts which are MLB/NHL/NCAAF --
the table's own comment reads "per sport: MLB ml 57.81% NHL ml 57.45%
NCAAF ml 58.68%" and carries NO NCAAB row. College basketball does not
trade anywhere near there. On the 2026-27 board the favourite side's
median breakeven is 71.43% and the dog side's 32.26% (n=807 each), because
the sport is full of mismatches the pro leagues do not have.

NCAAB ml prices are stored on 69.7% of games -- the missing ones are
low-major fixtures the books never post -- so the 80% gate fails for most
signals and the cross-sport constant was doing the work. `max(blind, be)`
then lifted the bar to the blind-home baseline (66.6%), which is still far
under the prices these signals fire at.

MEASURED COST, NCAAB, 90d:

  ncaab_home_efficiency_edge   hit 91.38%  n=58   be_n=32 (55%)
      its own prices imply 88.41%; bar used was 66.60, crediting
      +24.78pp where the truth is +2.97pp
  ncaab_confluence_home        hit 73.05%  n=167  be_n=132 (79%)
      own prices imply 74.24%; bar used 66.40, crediting +6.65pp on a
      signal that is -1.19pp BELOW its own price. It missed the coverage
      gate by ONE point and carried weight 0.670 while losing money.

A 91% hit rate is not an edge when the bet costs -760. The engine scores
winners; the bar has to be the price.

BLAST RADIUS, measured across all six sports before shipping (the repo's
own precedent -- the 2026-10-11 side-baseline change was shadowed for two
months because "a weight change moves every published pick"):

    MLB 0 of 7    NFL 0 of 2    NBA 0 of 4    NHL 0 of 6
    NCAAF 1 of 33   NCAAB 2 of 10        -> 3 of 62 signals

Every one is a DEMOTION; nothing gains weight. That is the shape that
matters: this change can only remove phantom edge, never manufacture it.
The NCAAF one is external:oddscrowd rl, 61.40% n=57, bar 52.38% -> 65.83%,
weight 0.684 -> 0.000 -- its picks were being priced at a flat -110 when
they fired around -191.

WHY A PARTIAL BAR IS CONSERVATIVE HERE, not merely closer: the firings
that CARRY a price are the lined games, i.e. the majors. The unpriced ones
are low-major mismatches, which are more lopsided still, so their true
breakeven is HIGHER than the priced subset's. The partial bar therefore
UNDER-states the real bar, and every error it makes is in the direction of
being too generous to the signal.

`be_measured` is still set only at full 80% coverage, so the stored
price_breakeven_pct column keeps its strict meaning and no reader can
mistake a partial bar for a full one.

OFFLINE: pure functions, no DB, no network.

    python test_partial_price_bar.py
"""
import os
import sys

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('SUPABASE_URL', 'http://localhost')
os.environ.setdefault('SUPABASE_KEY', 'test')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY', 'test')

fails = []


def chk(name, got, want):
    ok = got == want
    print(('  PASS ' if ok else '  FAIL ') + name + '  got=%r want=%r'
          % (got, want))
    if not ok:
        fails.append(name)


def close(name, got, want, tol=1e-4):
    ok = got is not None and abs(got - want) <= tol
    print(('  PASS ' if ok else '  FAIL ') + name + '  got=%r want~%r'
          % (got, want))
    if not ok:
        fails.append(name)


import refit_signal_registry as R        # noqa: E402
import ensemble_scorer as ES             # noqa: E402


def bar_for(n, be_n, be_mean, dom_market='ml'):
    """Reproduce the refit's bar selection for one signal."""
    if be_n >= max(10, int(n * 0.8)):
        return be_mean, True             # full coverage, storable
    if be_n >= R.PARTIAL_BE_MIN_N:
        return be_mean, False            # partial, tier input only
    return R._MARKET_BE_FALLBACK.get(dom_market), False


print('== THE FLOOR EXISTS AND MATCHES THE SCORER ===============')
chk('PARTIAL_BE_MIN_N is defined', hasattr(R, 'PARTIAL_BE_MIN_N'), True)
chk('and equals the scorer sample floor it is justified by',
    R.PARTIAL_BE_MIN_N, ES.SAMPLE_MIN_N)

print('== FULL COVERAGE IS UNCHANGED ============================')
be, storable = bar_for(201, 165, 0.7313)
close('80%+ coverage uses its own bar', be, 0.7313)
chk('and that bar is STORABLE', storable, True)

print('== PARTIAL COVERAGE NOW USES ITS OWN PRICES ==============')
# ncaab_home_efficiency_edge: n=58, be_n=32 (55%), own prices 88.41%
be, storable = bar_for(58, 32, 0.8841)
close('55% coverage uses the signal own bar', be, 0.8841)
chk('but it is NOT stored as a measured bar', storable, False)

# ncaab_confluence_home: missed the gate by one point of coverage
be, storable = bar_for(167, 132, 0.7424)
close('79% coverage uses its own bar', be, 0.7424)
chk('still not storable at 79%', storable, False)

print('== BELOW THE FLOOR, THE CONSTANT STILL APPLIES ===========')
be, _ = bar_for(100, 24, 0.9000)
close('be_n=24 falls back to the ml constant', be,
      R._MARKET_BE_FALLBACK['ml'])
be, _ = bar_for(100, 0, 0.0)
close('be_n=0 falls back too', be, R._MARKET_BE_FALLBACK['ml'])
be, _ = bar_for(100, 10, 0.9000)
close('a thin 10-firing bar is NOT trusted', be,
      R._MARKET_BE_FALLBACK['ml'])

print('== THE TWO NCAAB CASES THIS WAS WRITTEN FOR ==============')
# A signal BELOW its own price must earn zero, not 0.670.
blind_home = 0.664
w_old = ES.edge_weight(0.7305, 167, 'VALIDATED', False, blind_home,
                       R._MARKET_BE_FALLBACK['ml'])
w_new = ES.edge_weight(0.7305, 167, 'UNVALIDATED', False, blind_home,
                       0.7424)
chk('confluence_home was earning weight on a losing signal', w_old > 0.5,
    True)
chk('and now earns nothing, because it is below its own price',
    w_new, 0.0)

# A 91% hit rate on -760 favourites keeps a SMALL weight, not a big one.
w_old2 = ES.edge_weight(0.9138, 58, 'VALIDATED', False, 0.666,
                        R._MARKET_BE_FALLBACK['ml'])
w_new2 = ES.edge_weight(0.9138, 58, 'UNVALIDATED', False, 0.666, 0.8841)
chk('a 91% hit rate was earning a near-max weight', w_old2 > 0.8, True)
chk('and is now sized to its real +2.97pp, but NOT retired',
    0.0 < w_new2 < w_old2, True)

print('== THE BAR IS ALWAYS THE HARDER OF THE TWO ===============')
# edge_weight takes max(side_baseline, breakeven); a price bar must never
# LOWER a bar the side baseline already set.
chk('a price bar under the side baseline cannot lower it',
    ES.edge_weight(0.70, 100, 'VALIDATED', False, 0.666, 0.55),
    ES.edge_weight(0.70, 100, 'VALIDATED', False, 0.666, 0.666))
chk('and a price bar above it does raise it',
    ES.edge_weight(0.70, 100, 'VALIDATED', False, 0.666, 0.72), 0.0)

print('== NOTHING IS RETIRED =====================================')
# Andy's standing rule. A demotion must leave a signal able to earn its
# weight back when the measurement changes.
chk('a demoted signal still scores above its bar',
    ES.edge_weight(0.95, 58, 'UNVALIDATED', False, 0.666, 0.8841) > 0,
    True)
chk('ANTI_VALIDATED is the only hard zero',
    ES.edge_weight(0.95, 500, 'ANTI_VALIDATED', False, None, 0.52), 0.0)

print()
if fails:
    print('*** %d CASE(S) FAILED: %s ***' % (len(fails), ', '.join(fails)))
    print('    A failure here means a signal can be credited with edge it '
          'does not have, against a price from another sport.')
else:
    print('all partial price bar cases pass')
raise SystemExit(1 if fails else 0)
