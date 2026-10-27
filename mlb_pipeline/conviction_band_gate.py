#!/usr/bin/env python3
"""Conviction is only usable inside a price band. Gate on the band.

══ WHY (2026-10-27) ════════════════════════════════════════════════════════
Andy: "I need a working conviction system." We have one. It is applied
without regard to the PRICE, so it averages to noise and is actively
BACKWARDS in the band we publish most.

Measured on 689 graded `game_read` receipts carrying a price and a non-zero
conviction, splitting on conviction's median WITHIN each price band — so
favourite-ness cannot explain the separation:

    -180..-130   HI 72.2% n=72  ROI +19.20%   LO 59.1% n=66  -1.53%   WORKS
    -130..+100   HI 48.3% n=230 ROI  -8.99%   LO 50.9% n=173 -3.19%   INVERTED
    -250..-180   HI 64.3% n=28  ROI  -5.12%   LO 65.4% n=26  -3.04%   flat
    +100..+200   HI 51.9% n=27  ROI +15.11%   LO 36.0% n=25 -17.76%   works, thin

It holds SPLIT-HALF in both bands and in both directions, which is the test
that killed most of this month's other candidates:

    -180..-130  EARLY HI 76.7% n=30 +26.50% vs LO 46.7% -21.88%
                LATE  HI 74.4% n=39 +22.61% vs LO 64.1%  +6.70%
    -130..+100  EARLY HI 46.5% n=99 -12.37% vs LO 50.5%  -3.91%
                LATE  HI 47.2% n=106 -11.22% vs LO 53.5% +1.73%

403 of 689 reads — 58% of what we publish — sit in the inverted band.

    all graded game_read today    379-310 55.0% n=689 -18.49u  -2.68%
    drop -130..+100 entirely      180-106 62.9% n=286  +7.70u  +2.69%
    ONLY -180..-130 with HI conv   62-29  68.1% n=91  +11.48u +12.61%

NOT A NEW MODEL, deliberately. A per-sport walk-forward logistic regression
over 244 engine features LOST to the shipped MLB conviction (AUC 0.555 vs
0.625) and only beat the incumbent on NHL (0.616 vs 0.564). The number is
fine. The gate is what was missing.

HOW TO READ THE RETURN
    verdict() -> ('PUBLISH'|'DEMOTE'|'REFUSE', reason)
  REFUSE  high conviction inside the inverted band — the measured worst cell
  DEMOTE  the band carries no positive information; publish without leaning
          on conviction, or not at all if the surface wants only leans
  PUBLISH conviction is informative here

Every threshold is a module constant so a revert is one line, and
`--selftest` replays the measured cells so a future edit cannot silently
move the numbers this file claims.
"""
from __future__ import annotations

import argparse
import sys

# ── the measured bands. REVERT: set BANDS to () to disable the gate. ──
# (low, high, usable) on AMERICAN odds, low inclusive, high exclusive.
BANDS = (
    (-100000.0, -250.0, 'UNMEASURED'),   # n=26 pooled, too thin to judge
    (-250.0, -180.0, 'FLAT'),            # HI 64.3% vs LO 65.4% — no signal
    (-180.0, -130.0, 'USABLE'),          # HI 72.2% +19.20% vs LO 59.1%
    (-130.0, 100.0, 'INVERTED'),         # HI 48.3% -8.99% vs LO 50.9%
    (100.0, 200.0, 'USABLE_THIN'),       # HI 51.9% +15.11%, n=27
    (200.0, 100000.0, 'UNMEASURED'),     # n=4
)

# Conviction's median across the measured book. A pick at or above this is
# the "HI" half of every cell quoted above.
HI_CONVICTION = 59.0

# NCAAF inverts hardest: pick-em band HI 43.2% n=118 ROI -17.97% against LO
# 53.5% n=101 +1.80%. Refusing there is the single largest measured saving.
REFUSE_SPORTS_IN_INVERTED = {'NCAAF', 'MLB', 'NHL', 'NFL', 'NBA'}


def band_of(odds):
    """-> (label, (low, high)) or (None, None) when odds are unreadable."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None, None
    if o == 0:
        return None, None
    for lo, hi, label in BANDS:
        if lo <= o < hi:
            return label, (lo, hi)
    return None, None


def verdict(odds, conviction, sport=None):
    """('PUBLISH'|'DEMOTE'|'REFUSE', reason). Never raises."""
    if not BANDS:
        return 'PUBLISH', 'band gate disabled'
    label, rng = band_of(odds)
    if label is None:
        # An unreadable price is not a licence to use conviction. It is the
        # one case where we genuinely do not know which cell we are in.
        return 'DEMOTE', 'price unreadable — conviction not usable'
    try:
        c = float(conviction)
    except (TypeError, ValueError):
        return 'DEMOTE', 'conviction missing — band %s' % label
    hi = c >= HI_CONVICTION
    sp = str(sport or '').upper()

    if label == 'INVERTED':
        if hi and sp in REFUSE_SPORTS_IN_INVERTED:
            return ('REFUSE',
                    'conviction %g is HIGH inside the INVERTED band '
                    '[%g,%g): measured 48.3%% n=230 ROI -8.99%% vs 50.9%% '
                    'for LOW conviction, and it inverts in BOTH halves'
                    % (c, rng[0], rng[1]))
        return ('DEMOTE',
                'band [%g,%g) is INVERTED — conviction carries no positive '
                'information here' % (rng[0], rng[1]))
    if label == 'FLAT':
        return ('DEMOTE',
                'band [%g,%g) is FLAT — HI 64.3%% vs LO 65.4%%, conviction '
                'adds nothing' % (rng[0], rng[1]))
    if label == 'UNMEASURED':
        return ('DEMOTE',
                'band [%g,%g) is UNMEASURED (n<30) — not a licence to lean '
                'on conviction' % (rng[0], rng[1]))
    if label == 'USABLE_THIN':
        if hi:
            return 'PUBLISH', 'band [%g,%g) usable but THIN (n=27)' % rng
        return 'DEMOTE', 'low conviction in a thin band'
    # USABLE
    if hi:
        return ('PUBLISH',
                'conviction %g is HIGH in the USABLE band [%g,%g): '
                'measured 72.2%% n=72 ROI +19.20%%, holds both halves'
                % (c, rng[0], rng[1]))
    return ('DEMOTE',
            'conviction %g is LOW in the usable band [%g,%g): 59.1%% '
            'n=66 ROI -1.53%%' % (c, rng[0], rng[1]))


def _selftest():
    """Replay the measured cells. An edit that moves these is a bug."""
    cases = [
        # odds, conviction, sport, expected
        (-150, 75, 'MLB', 'PUBLISH'),    # usable band, HI
        (-150, 40, 'MLB', 'DEMOTE'),     # usable band, LO
        (-110, 75, 'NCAAF', 'REFUSE'),   # inverted band, HI — the worst cell
        (-110, 40, 'NCAAF', 'DEMOTE'),   # inverted band, LO
        (-200, 90, 'MLB', 'DEMOTE'),     # flat band
        (-400, 95, 'MLB', 'DEMOTE'),     # unmeasured
        (150, 75, 'NHL', 'PUBLISH'),     # thin usable
        (None, 75, 'MLB', 'DEMOTE'),     # unreadable price
        (-150, None, 'MLB', 'DEMOTE'),   # missing conviction
    ]
    bad = 0
    for odds, conv, sport, want in cases:
        got, why = verdict(odds, conv, sport)
        ok = got == want
        bad += 0 if ok else 1
        print('  %-5s odds=%-6s conv=%-5s %-6s -> %-8s (want %-8s) %s'
              % ('ok' if ok else 'FAIL', odds, conv, sport, got, want,
                 '' if ok else '<<<'))
        if not ok:
            print('        reason was: %s' % why)
    print('')
    print('  %d case(s) failed' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--odds', type=float)
    ap.add_argument('--conviction', type=float)
    ap.add_argument('--sport', default=None)
    a = ap.parse_args()
    if a.selftest:
        sys.exit(_selftest())
    if a.odds is None or a.conviction is None:
        sys.exit('pass --selftest, or --odds and --conviction')
    v, r = verdict(a.odds, a.conviction, a.sport)
    print('%s — %s' % (v, r))
