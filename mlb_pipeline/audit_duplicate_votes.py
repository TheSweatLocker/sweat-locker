#!/usr/bin/env python3
"""Are two ENABLED signals firing on the same number? Enforced, not argued.

== 2026-10-14 - WHY THIS EXISTS ==
ANDY: "we need to make duplicated independent now we need to fix those items."

Duplicate panelists have been found and fixed one at a time, by hand, five
times now, and each time the finding was correct and the NEXT one still got
through:

  2026-09-20  ncaaf sp_plus_pred_total == projected_total, counted as a second
              totals lens in ncaaf_sharp_fade_rules
  2026-09-26  GameDetailV2 showed "3 of 4 lenses lean UNDER" on a 1-1 split
  2026-09-29  the same bug in the SPREAD branch, which survived 09-20 because
              a comment asserted the spread columns were not aliases. They
              were (67/67 identical)
  2026-09-29  the first fix for THAT re-pointed at mc_expected_margin, which
              the simulator derives FROM projected_spread (r=+0.952)
  2026-10-14  nhl_mc_over_edge / _under_edge, duplicates of nhl_elo_*_edge:
              mean |diff| 0.0602 goals, same side 94.7%

Every one was caught by a person noticing. This checks it mechanically: pull
every ENABLED signal, work out which model number each one reads, measure those
numbers against each other on live rows, and flag any pair of signals that are
firing on effectively the same opinion.

TWO TESTS, because identity alone is not enough. The 09-29 note records exactly
why: mc_expected_margin is NEVER byte-identical to projected_spread (0/54) yet
correlates r=+0.95 with it, because the simulator takes it as input. So:

    IDENTICAL     mean |diff| below a market-relative epsilon
    DERIVED       |r| >= 0.90, or side agreement >= 90%

A DERIVED pair is still one opinion. "A lens is only a second lens if it
consumes different inputs" -- which code cannot verify, so a high correlation
is treated as guilty and needs a human to clear it.

Exit code 1 when any enabled pair is flagged, so this can gate a pipeline.

USAGE
    python audit_duplicate_votes.py
    python audit_duplicate_votes.py --sport NHL
    python audit_duplicate_votes.py --include-disabled
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys

import numpy as np
import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
CONTEXT = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
           'NHL': 'nhl_game_context', 'NBA': 'nba_game_context',
           'MLB': 'mlb_game_context', 'NCAAB': 'ncaab_game_context'}

#: Model numbers a signal can read. Plain context columns, plus jsonb paths.
MARGIN_SRC = {
    'projected_spread': ('col', 'projected_spread'),
    'v3_spread': ('col', 'v3_spread'),
    'v4_spread': ('col', 'v4_spread'),
    'model_pred_spread': ('col', 'model_pred_spread'),
    'sp_plus_pred_spread': ('col', 'sp_plus_pred_spread'),
    'epa_pred_spread': ('col', 'epa_pred_spread'),
    'jerry_pred_spread': ('col', 'jerry_pred_spread'),
    'panel_implied_margin': ('col', 'panel_implied_margin'),
    'mc_expected_margin': ('mc', 'mc_expected_margin'),
}
TOTAL_SRC = {
    'projected_total': ('col', 'projected_total'),
    'v3_total': ('col', 'v3_total'),
    'v4_total': ('col', 'v4_total'),
    'model_pred_total': ('col', 'model_pred_total'),
    'sp_plus_pred_total': ('col', 'sp_plus_pred_total'),
    'jerry_pred_total': ('col', 'jerry_pred_total'),
    'panel_implied_total': ('col', 'panel_implied_total'),
    'panel_pred_total': ('col', 'panel_pred_total'),
    'mc_expected_total': ('mc', 'mc_expected_total'),
    'mc_mean_total': ('mc', 'mc_mean_total'),
}
ALL_SRC = {**MARGIN_SRC, **TOTAL_SRC}

#: identity threshold, relative to the market's own scale for that market
EPS_FRAC = 0.01
CORR_HI = 0.90
SIDE_HI = 0.90
MIN_OVERLAP = 30


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                         timeout=180)
        if r.status_code not in (200, 206):
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def read_src(row, spec):
    kind, key = spec
    if kind == 'col':
        return _f(row.get(key))
    mp = row.get('mc_probabilities')
    if isinstance(mp, str):
        try:
            mp = json.loads(mp)
        except Exception:                                      # noqa: BLE001
            return None
    return _f(mp.get(key)) if isinstance(mp, dict) else None


def signals_for(sport, include_disabled):
    rows = page('signal_sources', {'select': 'signal_key,sport,enabled,'
                                             'condition_expr',
                                   'sport': f'eq.{sport}'})
    out = []
    for x in rows:
        if not include_disabled and x.get('enabled') is not True:
            continue
        ce = str(x.get('condition_expr') or '')
        hits = [name for name in ALL_SRC
                if re.search(r'\b' + re.escape(name) + r'\b', ce)]
        if len(hits) == 1:          # one model number -> attributable
            out.append((x['signal_key'], hits[0]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(CONTEXT))
    ap.add_argument('--include-disabled', action='store_true')
    a = ap.parse_args()
    sports = [a.sport] if a.sport else list(CONTEXT)
    print('DUPLICATE VOTE AUDIT — two signals firing on one opinion.')
    print(f'IDENTICAL: mean |diff| < {EPS_FRAC:.0%} of market sd. '
          f'DERIVED: |r| >= {CORR_HI} or side agreement >= {SIDE_HI:.0%}.')
    flagged = 0
    for sport in sports:
        sigs = signals_for(sport, a.include_disabled)
        if not sigs:
            print(f'\n{sport}: no single-source signals '
                  f'{"(incl. disabled)" if a.include_disabled else "enabled"}')
            continue
        ctx = page(CONTEXT[sport], {'select': '*'})
        print(f'\n{"="*78}\n{sport}: {len(sigs)} signals read one model number, '
              f'{len(ctx)} context rows')
        # group signals by the source they read
        by_src = {}
        for key, src in sigs:
            by_src.setdefault(src, []).append(key)
        srcs = sorted(by_src)
        for s in srcs:
            print(f'  {s:<24} <- {", ".join(sorted(by_src[s]))}')
        for s1, s2 in itertools.combinations(srcs, 2):
            m1 = s1 in MARGIN_SRC
            m2 = s2 in MARGIN_SRC
            if m1 != m2:
                continue                 # margin vs total: different markets
            pairs = []
            for r in ctx:
                v1, v2 = read_src(r, ALL_SRC[s1]), read_src(r, ALL_SRC[s2])
                if v1 is None or v2 is None:
                    continue
                mkt = _f(r.get('close_spread') if m1 else r.get('close_total'))
                pairs.append((v1, v2, mkt))
            if len(pairs) < MIN_OVERLAP:
                continue
            x = np.array([p[0] for p in pairs], float)
            y = np.array([p[1] for p in pairs], float)
            mk = [p[2] for p in pairs if p[2] is not None]
            scale = float(np.std(mk)) if len(mk) > 5 else float(np.std(y))
            eps = max(EPS_FRAC * max(scale, 1e-9), 0.005)
            md = float(np.mean(np.abs(x - y)))
            r = (float(np.corrcoef(x, y)[0, 1])
                 if x.std() > 0 and y.std() > 0 else float('nan'))
            side = None
            both = [(a_, b_, c) for a_, b_, c in pairs if c is not None]
            if both:
                agree = sum(1 for a_, b_, c in both
                            if (a_ > c) == (b_ > c))
                side = agree / len(both)
            verdict = None
            if md < eps:
                verdict = 'IDENTICAL'
            elif (not math.isnan(r) and abs(r) >= CORR_HI) or \
                 (side is not None and side >= SIDE_HI):
                verdict = 'DERIVED — one opinion'
            if verdict:
                flagged += 1
                print(f'\n  ** {verdict} **  {s1}  vs  {s2}')
                print(f'     n={len(pairs)}  mean|diff| {md:.4f} (eps {eps:.4f})'
                      f'  r={r:+.3f}'
                      + (f'  same side {100*side:.1f}%' if side else ''))
                print(f'     signals on {s1}: {", ".join(sorted(by_src[s1]))}')
                print(f'     signals on {s2}: {", ".join(sorted(by_src[s2]))}')
                print(f'     -> these fire as separate confirmations of the '
                      f'same number')
    print(f'\n{"="*78}')
    if flagged:
        print(f'{flagged} duplicate pair(s) among '
              f'{"all" if a.include_disabled else "ENABLED"} signals. Each is '
              f'manufactured corroboration: disable one side, or make it read '
              f'different INPUTS.')
        return 1
    print('No duplicate pair among '
          f'{"all" if a.include_disabled else "enabled"} signals.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
