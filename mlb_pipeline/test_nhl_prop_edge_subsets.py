#!/usr/bin/env python3
"""Is there ANY NHL prop subset with edge at its real price?

== 2026-10-16 - WHY ==
ANDY: "inmorvement nhl props".

diagnose_nhl_props.py settled what the 41% is and is not:
  * the grader is CORRECT — 24,058 recomputable rows, 24,058 agree, 0 disagree
  * overs 24.5% (n=14,983), unders 68.2% (n=9,075); every prop type the same way
  * priced at the real book odds, EVERY bucket is at or below zero ROI:
        + dog 36.0% -0.1% | -100..-149 54.9% -0.4% | -150..-199 61.6% -2.4%
        -200..-299 70.5% 0.0% | -300 or worse 83.4% -3.8%
        ALL UNDERS 68.2% and -2.1% ROI
  * all 24,058 are tier COVERAGE — the info layer, not published plays

So 41% was never a record. It is the hit rate of a coverage layer full of
heavy-juice unders, and the market has priced it correctly. Flipping to unders
is 59.0% and still loses.

That makes the improvement question narrow and answerable: does ANY slice of
these props beat its own price out of sample? The signal columns the pipeline
already stores are the candidates — conviction, refit_conviction,
player_l10_hit_count, player_l5_hit_count, player_season_hit_pct, the extreme
flags, lineup_state, and position.

== DISCIPLINE ==
Temporal split; thresholds chosen on TRAIN only, reported on TEST. Graded in
UNITS at the actual book price for the side taken, never on hit rate. Wilson CI
and n beside everything. A subset must beat ZERO on test — not beat 50%, not
beat the overall book — because the alternative to a prop is not betting it.
Bonferroni printed for the number of slices tried.

Expect mostly nothing. On a market this efficiently priced a null is the
likely and useful answer: it says NHL props stay info-only until we feed them
information the book lacks, rather than that a threshold needs tuning.

USAGE
    python test_nhl_prop_edge_subsets.py
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict

import numpy as np
import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
TRAIN_FRAC = 0.60


def page(t, p):
    out, off = [], 0
    while True:
        q = dict(p, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def payout(o):
    o = f(o)
    if o is None:
        return None
    return (100 / -o) if o < 0 else (o / 100)


def wilson(w, n):
    if n == 0:
        return None, None
    z, p = 1.96, w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def units(rows):
    w = l = 0
    u = 0.0
    for r in rows:
        if r['win']:
            w += 1
            u += r['pay']
        else:
            l += 1
            u -= 1.0
    return w, l, u


def show(label, rows, indent='    '):
    if len(rows) < 25:
        print(f'{indent}{label:<38}n={len(rows)} too few')
        return None
    w, l, u = units(rows)
    n = w + l
    lo, hi = wilson(w, n)
    roi = 100 * u / n
    print(f'{indent}{label:<38}{w}-{l}  {100*w/n:5.1f}%  '
          f'CI[{lo:.1f},{hi:.1f}]  {u:+8.2f}u  ROI {roi:+6.1f}%  n={n}')
    return roi


def main() -> int:
    raw = page('nhl_pipeline_props', {'select': '*'})
    rows = []
    for x in raw:
        if str(x.get('result')) not in ('Win', 'Loss'):
            continue
        d = str(x.get('direction') or '').lower()
        o = x.get('book_under_odds') if d == 'under' else x.get('book_over_odds')
        pay = payout(o)
        if pay is None:
            continue
        rows.append({
            'date': str(x.get('game_date'))[:10],
            'win': str(x.get('result')) == 'Win',
            'pay': pay, 'odds': f(o), 'dir': d,
            'ptype': str(x.get('prop_type') or ''),
            'conv': f(x.get('conviction')),
            'refit': f(x.get('refit_conviction')),
            'l10': f(x.get('player_l10_hit_count')),
            'l5': f(x.get('player_l5_hit_count')),
            'season': f(x.get('player_season_hit_pct')),
            'pos': str(x.get('player_position') or ''),
            'lineup': str(x.get('lineup_state') or ''),
        })
    rows.sort(key=lambda r: r['date'])
    print(f'NHL props graded WITH a price for the side taken: {len(rows)}')
    cut = int(len(rows) * TRAIN_FRAC)
    tr, te = rows[:cut], rows[cut:]
    print(f'temporal split at {rows[cut]["date"]}: train {len(tr)}, '
          f'test {len(te)}')
    print('\nBASELINE, all props, graded in units at the real price:')
    show('TRAIN all', tr)
    show('TEST  all', te)

    slices = []

    def add(name, fn):
        slices.append((name, fn))

    # direction x price band — the only structure found so far
    for d in ('under', 'over'):
        for lo_, hi_ in ((-149, 0), (-199, -150), (-299, -200),
                         (-99999, -300)):
            add(f'{d} @ {hi_}..{lo_}',
                lambda r, d=d, lo_=lo_, hi_=hi_:
                r['dir'] == d and r['odds'] is not None
                and hi_ <= r['odds'] <= lo_)
        add(f'{d} @ plus money',
            lambda r, d=d: r['dir'] == d and (r['odds'] or 0) > 0)
    # signal columns
    for thr in (6, 7, 8):
        add(f'l10_hit_count >= {thr}',
            lambda r, t=thr: r['l10'] is not None and r['l10'] >= t)
        add(f'l10_hit_count <= {10-thr}',
            lambda r, t=thr: r['l10'] is not None and r['l10'] <= (10 - t))
    for thr in (60, 70, 80):
        add(f'conviction >= {thr}',
            lambda r, t=thr: r['conv'] is not None and r['conv'] >= t)
    for thr in (0.65, 0.75):
        add(f'season_hit_pct >= {thr}',
            lambda r, t=thr: r['season'] is not None and r['season'] >= t)
    for pt in ('sog_under', 'points_under', 'goals_under', 'assists_under',
               'sog_over'):
        add(f'ptype={pt}', lambda r, pt=pt: r['ptype'] == pt)

    bonf = 0.05 / len(slices)
    print(f'\n{len(slices)} slices tried. Bonferroni p<{bonf:.4f}. '
          f'A slice must beat ZERO ROI on TEST to matter.')
    print('\n  TRAIN (ceiling)                        /  TEST (the result)')
    winners = []
    for name, fn in slices:
        str_rows = [r for r in tr if fn(r)]
        ste_rows = [r for r in te if fn(r)]
        if len(str_rows) < 25 or len(ste_rows) < 25:
            continue
        print(f'  {name}')
        roi_tr = show('train', str_rows, indent='      ')
        roi_te = show('TEST ', ste_rows, indent='      ')
        if roi_te is not None and roi_te > 0 and roi_tr is not None \
                and roi_tr > 0:
            winners.append((name, roi_tr, roi_te, len(ste_rows)))

    print('\n' + '=' * 70)
    if winners:
        print('POSITIVE ON BOTH TRAIN AND TEST:')
        for n_, a, b, nn in sorted(winners, key=lambda x: -x[2]):
            print(f'  {n_:<34} train {a:+6.1f}%  TEST {b:+6.1f}%  n={nn}')
        print('\nStill not proven — a slice positive on both halves of one '
              'season, chosen from a list this long, is what chance produces. '
              'Shadow forward before anything is published.')
    else:
        print('NOTHING is positive on both halves. NHL props carry no edge at '
              'their price in any slice tried, which is consistent with the '
              'book pricing them correctly. They stay an INFO layer until fed '
              'information the book lacks — confirmed-goalie timing and late '
              'scratches being the candidates already identified. This is an '
              'answer, not a failure to find one.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
