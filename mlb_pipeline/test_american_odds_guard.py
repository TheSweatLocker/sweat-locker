# -*- coding: utf-8 -*-
"""The breakeven bar must reject a non-price and KEEP an extreme price.

== WHY (2026-11-27) ====================================================
`refit_signal_registry._american_to_be` feeds `_firing_breakeven`, whose
per-firing values average into `be_sum / be_n` -> `price_breakeven_pct`
and, since 2026-11-25, the live `edge_weight` bar. Two opposite mistakes
are both live hazards there, and this suite pins the line between them.

REJECT: anything inside +/-100. American odds are |price| >= 100 by
definition; -100 and +100 are the same even-money price and nothing lies
between them. 333 of 4,987 priced NCAAB 2024-25 pairs (6.7%) carry one --
`-125 / 60`, `-81 / -116`, `15 / -121`, seven literal zeros. The old guard
checked only `== 0`, so a stored -81 became a 44.75% breakeven: a
FAVOURITE priced below a coin flip. A bar pushed DOWN credits edge that
was never there.

KEEP: an extreme price. 452 NCAAB rows carry moneylines past +/-5000, out
to -100000/+3300. Those are real: all 452 have a coherent overround
(1.01-1.06), sit on a mean 26.6-point closing spread, and the favourite
won 450 of 452. Rejecting them would strip out exactly the prices a
favourite signal cannot beat and flatter every such signal -- cherry-
picking in the direction that manufactures edge. I called them
placeholders first and the overround corrected me; this suite exists so
nobody "cleans" them later.

Offline, pure function, no network, no DB.

    python test_american_odds_guard.py
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                              # noqa: BLE001
    pass

# refit_signal_registry does `os.environ['SUPABASE_URL']` at module scope, so
# importing it without a .env raises KeyError and this suite would exit 1
# after 0s in CI while looking like a real guard failure -- exactly
# [[feedback_exit_1_after_0s_is_an_import_crash]], which cost a day of
# daily_card runs. Placeholders are safe because every function under test
# here is pure: nothing below opens a socket or touches the DB. Deliberately
# NOT a copy of the function -- a test of a copy pins the copy
# ([[feedback_comment_asserting_a_measurement]]).
os.environ.setdefault('SUPABASE_URL', 'http://test.invalid')
os.environ.setdefault('SUPABASE_KEY', 'test-key-not-used')

import refit_signal_registry as R                            # noqa: E402

FAILS: list = []


def chk(label, got, want, tol=1e-6):
    if want is None:
        ok = got is None
    else:
        ok = got is not None and abs(got - want) <= tol
    print(f'  {"PASS" if ok else "FAIL"}  {label}')
    if not ok:
        print(f'          got {got!r}, want {want!r}')
        FAILS.append(label)


print('REJECTED -- not American odds at all')
for v in (0, 0.0, 15, 60, 82, 99, 99.9, -76, -81, -99, -0.5, None, 'x', ''):
    chk(f'{v!r} -> None', R._american_to_be(v), None)

print('\nACCEPTED -- ordinary prices')
chk('-110 -> 52.38%', R._american_to_be(-110), 110.0 / 210.0)
chk('+100 -> 50.00%', R._american_to_be(100), 0.5)
chk('-100 -> 50.00%', R._american_to_be(-100), 0.5)
chk('+150 -> 40.00%', R._american_to_be(150), 0.4)
chk('-250 -> 71.43%', R._american_to_be(-250), 250.0 / 350.0)
chk('string "-110" parses', R._american_to_be('-110'), 110.0 / 210.0)

print('\nACCEPTED -- EXTREME but real (do not "clean" these)')
chk('-7320 -> 98.65%', R._american_to_be(-7320), 7320.0 / 7420.0)
chk('-100000 -> 99.90%', R._american_to_be(-100000), 100000.0 / 100100.0)
chk('+3300 -> 2.94%', R._american_to_be(3300), 100.0 / 3400.0)
chk('+5000 -> 1.96%', R._american_to_be(5000), 100.0 / 5100.0)

print('\nthe boundary is exactly 100, inclusive')
chk('+100 accepted', R._american_to_be(100), 0.5)
chk('+100.0 accepted', R._american_to_be(100.0), 0.5)
chk('+99.99 rejected', R._american_to_be(99.99), None)
chk('-99.99 rejected', R._american_to_be(-99.99), None)

print('\na rejected price must not reach the bar through _firing_breakeven')
_cols = {'ml': ('home_ml_close', 'away_ml_close')}
chk('HOME side on a -81 row -> None',
    R._firing_breakeven('ml', 'HOME_ML', {'home_ml_close': -81,
                                          'away_ml_close': -116}, _cols),
    None)
chk('AWAY side on that row still prices (-116 is valid)',
    R._firing_breakeven('ml', 'AWAY_ML', {'home_ml_close': -81,
                                          'away_ml_close': -116}, _cols),
    116.0 / 216.0)
chk('HOME side on a -100000 row DOES price',
    R._firing_breakeven('ml', 'HOME_ML', {'home_ml_close': -100000,
                                          'away_ml_close': 3300}, _cols),
    100000.0 / 100100.0)
chk('a market with no resolved columns -> None',
    R._firing_breakeven('total', 'OVER', {'home_ml_close': -110}, _cols),
    None)

print()
if FAILS:
    print(f'{len(FAILS)} FAILED:')
    for f in FAILS:
        print(f'  * {f}')
    sys.exit(1)
print('all american-odds guard checks pass')
