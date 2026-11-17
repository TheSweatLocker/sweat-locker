#!/usr/bin/env python3
"""Tests for the receipt line guard — the bug that keeps coming back.

== WHY THIS FILE EXISTS (2026-11-16) ======================================
ANDY: "for naythign that we just dfound it needs to be fixed so it stops
fucking ahppening, same shit conotinue to ahpen day after day this is
unsat".

A bare spread label has reached the public record at least three times
(2026-10-31 twelve NBA receipts, 2026-11-08 'Boston Celtics ATS' graded
VOID, 2026-11-16 the Golden State POTD). Each time it was repaired by hand
and the guard that was supposed to stop it was never the thing that got
fixed, because the guard was never WRONG -- it was never ASKED:
`public_receipt.unactionable` keys on `market`, and potd_rows writes
`market='potd'`, a SURFACE name.

So the fix is a parser plus an effective-market resolution, and THIS is the
part that stops it coming back: executable cases, run in CI, including the
adversarial ones that a substring parser gets wrong.

    python test_receipt_line_guard.py
"""
from __future__ import annotations

import sys

from public_receipt import (_bet_market, _effective_market, recover_line,
                            unactionable)

FAILED = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILED.append(f'{name}: got {got!r}, want {want!r}')


# ── 1. the market parser ────────────────────────────────────────────────
# Real label shapes, taken from public_receipts.
for label, want in [
    ('Braves ML (Jerry 88/100)',                    'ml'),
    ('Golden State Warriors ATS -3.5 (Jerry 55/100)', 'spread'),
    ('Golden State Warriors ATS (Jerry 55/100)',    'spread'),
    ('BUF ATS -8 (Jerry 65/100)',                   'spread'),
    ('New York Knicks ATS -15.5 (Jerry 53/100)',    'spread'),
    ('Under 148.5',                                 'total'),
    ('Over 6.5',                                    'total'),
    ('Tampa Bay Lightning RL',                      'rl'),
    ('Winnipeg Jets PL',                            'puckline'),
    # A bare +/-1.5 is the puckline in NHL, the run line in MLB and an
    # ordinary spread in NBA, and the LABEL cannot tell you which. So this
    # resolves to the generic 'spread'. That is harmless for the guard,
    # which requires a line for all of total/rl/spread/puckline alike --
    # and guessing 'puckline' from the number would be inventing a sport.
    ('Buffalo Sabres -1.5 (NHL LEAN 56/100)',       'spread'),
    ('UMass -2.5 (NCAAF LEAN 61/100)',              'spread'),
    ('Tulsa -5.5',                                  'spread'),
    ('Brooklyn Nets +4.5',                          'spread'),
    ('No Play (discipline pass)',                   None),
]:
    check(f'_bet_market({label!r})', _bet_market(label), want)

# ── 2. THE SUBSTRING TRAP ───────────────────────────────────────────────
# `'ats' in label` matches every one of these. Arizona, Kentucky,
# Villanova and Kansas State all field Wildcats; Cincinnati fields
# Bearcats. A moneyline on any of them must NOT be read as a spread, or
# the guard demands a line the pick never had and refuses a valid receipt.
for label in ('Arizona Wildcats ML', 'Kentucky Wildcats ML',
              'Cincinnati Bearcats ML', 'Kansas St Wildcats ML',
              'Villanova Wildcats ML', 'Northwestern Wildcats ML'):
    check(f'_bet_market({label!r}) must not be a spread',
          _bet_market(label), 'ml')

# 'pl' and 'rl' as substrings must not fire either.
for label, want in [('Philadelphia Flyers ML', 'ml'),
                    ('Orlando Magic ML', 'ml'),
                    ('Portland Trail Blazers ML', 'ml')]:
    check(f'_bet_market({label!r})', _bet_market(label), want)

# ── 3. the effective market reaches through a surface name ──────────────
check('potd spread resolves to spread',
      _effective_market({'market': 'potd',
                         'pick_label': 'Golden State Warriors ATS -3.5',
                         'audit': {'bet_market': 'spread'}}), 'spread')
check('potd with a null audit falls back to the label',
      _effective_market({'market': 'potd',
                         'pick_label': 'Golden State Warriors ATS',
                         'audit': {'bet_market': None}}), 'spread')
check('potd moneyline stays a moneyline',
      _effective_market({'market': 'potd',
                         'pick_label': 'Ole Miss ML',
                         'audit': {'bet_market': 'ml'}}), 'ml')
check('a real market code is returned untouched',
      _effective_market({'market': 'rl', 'pick_label': 'Tulsa -5.5'}), 'rl')
check('a parlay is NOT resolved to a line market',
      _effective_market({'market': 'chalk_parlay',
                         'pick_label': 'chalk_parlay (2 legs)'}),
      'chalk_parlay')
check('a fight is NOT resolved to a line market',
      _effective_market({'market': 'fight', 'pick_label': 'Jones DEC'}),
      'fight')

# ── 4. unactionable — the guard itself ──────────────────────────────────
check('BARE POTD SPREAD IS REFUSED',
      unactionable({'market': 'potd',
                    'pick_label': 'Golden State Warriors ATS (Jerry 55/100)',
                    'pick_line': None,
                    'audit': {'bet_market': 'spread'}}), True)
check('priced POTD spread is accepted',
      unactionable({'market': 'potd',
                    'pick_label': 'Golden State Warriors ATS -3.5',
                    'pick_line': -3.5,
                    'audit': {'bet_market': 'spread'}}), False)
check('POTD moneyline is accepted with no line',
      unactionable({'market': 'potd', 'pick_label': 'Ole Miss ML',
                    'pick_line': None,
                    'audit': {'bet_market': 'ml'}}), False)
check('bare NHL puckline label is accepted (fixed +/-1.5, no number)',
      unactionable({'market': 'rl', 'pick_label': 'Tampa Bay Lightning RL',
                    'pick_line': -1.5}), False)
check('parlay with no line is accepted',
      unactionable({'market': 'chalk_parlay', 'pick_line': None,
                    'pick_label': 'chalk_parlay (2 legs)'}), False)
check('bare total is refused',
      unactionable({'market': 'total', 'pick_label': 'Over',
                    'pick_line': None}), True)

# ── 5. recover_line — pull the number out of the label ──────────────────
check('recovers from a POTD label whose number is not last',
      recover_line({'market': 'potd',
                    'pick_label': 'Golden State Warriors ATS -3.5 '
                                  '(Jerry 55/100)',
                    'pick_line': None,
                    'audit': {'bet_market': 'spread'}}).get('pick_line'),
      -3.5)
check('recovers a trailing total',
      recover_line({'market': 'total', 'pick_label': 'Under 148.5',
                    'pick_line': None}).get('pick_line'), 148.5)
check('recovers from "BUF ATS -8 (Jerry 65/100)"',
      recover_line({'market': 'potd', 'pick_label': 'BUF ATS -8 '
                                                    '(Jerry 65/100)',
                    'pick_line': None,
                    'audit': {'bet_market': 'spread'}}).get('pick_line'), -8)
check('does NOT invent a line for a bare label',
      recover_line({'market': 'potd',
                    'pick_label': 'Golden State Warriors ATS (Jerry 55/100)',
                    'pick_line': None,
                    'audit': {'bet_market': 'spread'}}).get('pick_line'),
      None)
check('does NOT read the 76 out of 76ers',
      recover_line({'market': 'ml', 'pick_label': 'Philadelphia 76ers ML',
                    'pick_line': None}).get('pick_line'), None)
check('an existing line is never overwritten',
      recover_line({'market': 'total', 'pick_label': 'Under 148.5',
                    'pick_line': 150.5}).get('pick_line'), 150.5)

if FAILED:
    print(f'FAILED {len(FAILED)} case(s):')
    for f in FAILED:
        print(f'  - {f}')
    sys.exit(1)
print('receipt line guard: all cases pass')
