#!/usr/bin/env python3
"""WALK-FORWARD VALIDATION — the harness every claim should pass through.

══ WHY (2026-10-26) ════════════════════════════════════════════════════════
Andy: "if we can set a baseline for new method and backtest in all weeks we
can assess how the engine would have did in that moment in that week based on
that week's data, I feel like that's the key to finding a true pattern".

He is right, and it is stronger than what we had been doing. A single
discovery/holdout split gives ONE number, and one number cannot tell a stable
edge from a lucky stretch. It also hides parameter leakage: on 2026-10-26 a
crowd-override rule looked better than a plain base-rate rule on the data it
was fitted to (57.3% vs 52.0%) and lost on BOTH held-out weeks (53.8% vs
69.2%, 66.7% vs 83.3%). The holdout caught it that time. It would not always.

Walk-forward closes both gaps:

  * for each period, the rule may only see data from BEFORE that period --
    including when choosing its own thresholds, which is where leakage
    usually hides
  * you get a hit rate PER PERIOD, so stability is visible rather than
    averaged away

A rule that is real looks boring here: a bit above breakeven most weeks. A
rule that is fitted noise looks spectacular in a couple of periods and
negative in the rest, with the same overall average.

USAGE
    from walk_forward import walk_forward, Period

    def factory(train):
        '''Return a callable rule from training rows ONLY.'''
        pick_home = sum(r['home_cov'] for r in train) > len(train) / 2
        return lambda row: 'HOME' if pick_home else 'AWAY'

    walk_forward(rows, factory, grade, period='week', min_train=150)

`grade(row, call)` returns True (win), False (loss) or None (push/no play).
Returning None for "no play" is how a selective rule is honestly scored: its
hit rate is over the plays it MADE, and its coverage is reported alongside.
"""
from __future__ import annotations

import collections
import datetime as dt
import math


def wilson(w: int, n: int) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = w / n
    z = 1.96
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (100 * (c - m) / d, 100 * (c + m) / d)


def period_key(date_str: str, period: str = 'week') -> str:
    """Bucket a YYYY-MM-DD into a comparable period label."""
    try:
        d = dt.date.fromisoformat(str(date_str)[:10])
    except ValueError:
        return str(date_str)[:10]
    if period == 'day':
        return d.isoformat()
    if period == 'month':
        return d.strftime('%Y-%m')
    if period == 'week':
        iso = d.isocalendar()
        return '%d-W%02d' % (iso[0], iso[1])
    if period in ('year', 'season'):
        return '%04d' % d.year
    # 2026-10-26: this used to fall through to ISO week for ANY unknown
    # value, so period='year' silently ran WEEKLY and the output said
    # "seasons 120/250" when 250 was a week count. A silent default that
    # mislabels its own output is the same defect class as a swallowed
    # non-200 -- so an unknown period now raises instead.
    raise ValueError(
        "unknown period %r -- use 'day', 'week', 'month' or 'year'" % period)


def walk_forward(rows, rule_factory, grade, period='week', min_train=150,
                 date_field='date', label='rule', breakeven=52.38,
                 verbose=True):
    """Step forward in time; never let a period see itself or its future.

    Returns a dict with per-period results and the pooled out-of-sample
    record. Pooled numbers here are genuinely out-of-sample: every play was
    graded by a rule built without it.
    """
    rows = [r for r in rows if r.get(date_field)]
    rows.sort(key=lambda r: str(r[date_field]))
    buckets = collections.OrderedDict()
    for r in rows:
        buckets.setdefault(period_key(str(r[date_field]), period), []).append(r)

    per, W, L, SKIP, trained_from = [], 0, 0, 0, None
    for i, (pk, test) in enumerate(buckets.items()):
        train = [r for r in rows
                 if period_key(str(r[date_field]), period) < pk]
        if len(train) < min_train:
            SKIP += len(test)
            continue
        if trained_from is None:
            trained_from = pk
        rule = rule_factory(train)
        w = l = noplay = 0
        for r in test:
            call = rule(r)
            if call is None:
                noplay += 1
                continue
            g = grade(r, call)
            if g is True:
                w += 1
            elif g is False:
                l += 1
            else:
                noplay += 1
        W += w
        L += l
        per.append({'period': pk, 'w': w, 'l': l, 'noplay': noplay,
                    'n_train': len(train), 'n_test': len(test)})

    n = W + L
    lo, hi = wilson(W, n)
    if verbose:
        print('')
        print('  WALK-FORWARD · %s · period=%s · min_train=%d'
              % (label, period, min_train))
        print('  %-12s %-9s %-7s %-8s %s'
              % ('PERIOD', 'record', 'hit', 'no-play', 'train n'))
        for p in per:
            m = p['w'] + p['l']
            print('  %-12s %3d-%-3d   %-7s %-8d %d'
                  % (p['period'], p['w'], p['l'],
                     ('%.1f%%' % (100 * p['w'] / m)) if m else '   -',
                     p['noplay'], p['n_train']))
        pos = sum(1 for p in per
                  if (p['w'] + p['l']) and
                  100 * p['w'] / (p['w'] + p['l']) > breakeven)
        rated = sum(1 for p in per if p['w'] + p['l'])
        print('  %-12s %3d-%-3d   %-7s' % ('POOLED OOS', W, L,
                                           ('%.1f%%' % (100 * W / n))
                                           if n else '   -'))
        if n:
            print('  CI[%.1f, %.1f]   breakeven %.2f%%   '
                  'periods above breakeven: %d of %d'
                  % (lo, hi, breakeven, pos, rated))
            verdict = ('STABLE EDGE' if lo > breakeven and pos >= 0.6 * rated
                       else 'POSITIVE BUT NOT ESTABLISHED'
                       if 100 * W / n > breakeven
                       else 'NO EDGE')
            print('  VERDICT: %s' % verdict)
        if SKIP:
            print('  (%d plays skipped while the training window filled)'
                  % SKIP)
    return {'per_period': per, 'wins': W, 'losses': L, 'ci': (lo, hi),
            'n': n, 'skipped': SKIP, 'trained_from': trained_from}
