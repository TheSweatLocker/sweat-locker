#!/usr/bin/env python3
"""NHL props are 41.0% on 24,058 graded. That is structural. Find it.

== 2026-10-16 - WHY ==
ANDY: "inmorvement nhl props".

nhl_pipeline_props all-time: 9,868-14,190 = 41.0%, plus 4,273 ungraded.
At n=24,058 a 41% rate is ~28 standard deviations below a coin flip, so this is
not variance and not bad luck — something is systematically picking the wrong
side. The size of the sample is the gift here: a real defect at this n shows up
in every cut.

Candidate causes, each testable:
  * DIRECTION INVERSION — the over/under flag indexes the wrong side
  * OVER-BIAS — nearly every prop is an OVER and NHL overs lose
    (project_nhl_prop_over_bias_1009 says over-bias, info-only)
  * GRADER SIGN — actual_value compared to the line the wrong way round
  * TIER INVERSION — the better the tier the worse the result

The decisive test for an inversion is whether the COMPLEMENT is symmetric:
if OVER hits 41% and UNDER hits 59% on comparable volume, the information is
there and the side selection is backwards. If BOTH hit ~41%, it is the grader
or the line, not the pick.

USAGE
    python diagnose_nhl_props.py
"""
from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H


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


def wilson(w, n):
    if n == 0:
        return None, None
    z, p = 1.96, w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def rec(rows):
    w = sum(1 for x in rows if str(x.get('result')) == 'Win')
    l = sum(1 for x in rows if str(x.get('result')) == 'Loss')
    if w + l == 0:
        return None
    lo, hi = wilson(w, w + l)
    return w, l, 100 * w / (w + l), lo, hi


def show(label, rows, indent='  '):
    r = rec(rows)
    if not r:
        print(f'{indent}{label:<34} no graded rows')
        return
    w, l, p, lo, hi = r
    print(f'{indent}{label:<34}{w}-{l}  {p:5.1f}%  CI[{lo:.1f},{hi:.1f}]  '
          f'n={w+l}')


def main() -> int:
    rows = page('nhl_pipeline_props', {'select': '*'})
    print(f'nhl_pipeline_props rows: {len(rows)}')
    gd = [x for x in rows if str(x.get('result')) in ('Win', 'Loss')]
    print(f'graded: {len(gd)}')
    show('ALL NHL PROPS', gd)

    cols = sorted(rows[0].keys()) if rows else []
    print(f'\ncolumns: {cols}')

    # 1. DIRECTION — the inversion test
    print('\n1. BY DIRECTION (the inversion test)')
    dirs = Counter(str(x.get('direction')) for x in gd)
    print(f'   direction values: {dict(dirs)}')
    for d in sorted({str(x.get('direction')) for x in gd}):
        show(f'direction={d}', [x for x in gd if str(x.get('direction')) == d])
    print('   If OVER ~41% and UNDER ~59% the side selection is backwards.')
    print('   If BOTH ~41% the defect is the grader or the line, not the pick.')

    # 2. TIER
    print('\n2. BY TIER')
    for t in sorted({str(x.get('tier')) for x in gd}):
        show(f'tier={t}', [x for x in gd if str(x.get('tier')) == t])

    # 3. PROP TYPE — top by volume
    print('\n3. BY PROP TYPE (top 14 by volume)')
    cnt = Counter(str(x.get('prop_type')) for x in gd)
    for pt, _n in cnt.most_common(14):
        show(pt, [x for x in gd if str(x.get('prop_type')) == pt])

    # 4. GRADER SANITY — recompute from actual_value vs prop_line
    print('\n4. GRADER SANITY — recompute the result from actual_value')
    ok = bad = nocols = 0
    mism = []
    for x in gd:
        av, pl = f(x.get('final_value')), f(x.get('prop_line'))
        d = str(x.get('direction') or '').lower()
        if av is None or pl is None or d not in ('over', 'under'):
            nocols += 1
            continue
        if av == pl:
            continue
        went_over = av > pl
        should = 'Win' if (went_over == (d == 'over')) else 'Loss'
        if should == str(x.get('result')):
            ok += 1
        else:
            bad += 1
            if len(mism) < 8:
                mism.append(x)
    print(f'   recomputable {ok+bad}, agree {ok}, DISAGREE {bad}, '
          f'missing fields {nocols}')
    if ok + bad == 0:
        print('   -> NOTHING RECOMPUTABLE. No conclusion is drawn from zero '
              'rows; the earlier version of this script printed one, which was '
              'unfounded (feedback_zero_rows_is_a_wiring_error).')
    if bad:
        print(f'   -> {100*bad/max(ok+bad,1):.1f}% of recomputable rows are '
              f'graded against their own numbers. Examples:')
        for x in mism:
            print(f"      {str(x.get('player_name'))[:18]:<19}"
                  f"{str(x.get('prop_type'))[:16]:<17}"
                  f"{str(x.get('direction')):<6}line {x.get('prop_line')}  "
                  f"actual {x.get('final_value')}  stored {x.get('result')}")
    elif ok:
        print('   -> grader agrees with its own inputs on every recomputable '
              'row. The defect is the PICK, not the grading.')

    # 5. the complement: how often did the OTHER side win?
    print('\n5. WOULD THE OPPOSITE SIDE HAVE WON? (direction-only control)')
    flip_w = flip_l = 0
    for x in gd:
        av, pl = f(x.get('final_value')), f(x.get('prop_line'))
        d = str(x.get('direction') or '').lower()
        if av is None or pl is None or d not in ('over', 'under') or av == pl:
            continue
        went_over = av > pl
        opp = 'under' if d == 'over' else 'over'
        if went_over == (opp == 'over'):
            flip_w += 1
        else:
            flip_l += 1
    if flip_w + flip_l:
        lo, hi = wilson(flip_w, flip_w + flip_l)
        print(f'   FLIPPED: {flip_w}-{flip_l}  '
              f'{100*flip_w/(flip_w+flip_l):.1f}%  CI[{lo:.1f},{hi:.1f}]  '
              f'n={flip_w+flip_l}')
    # 6. PRICE — is the UNDER side actually payable?
    print('')
    print('6. THE UNDER SIDE AT ITS PRICE (68.2% means nothing at -250)')
    und = [x for x in gd if str(x.get('direction')).lower() == 'under']
    buckets = defaultdict(lambda: [0, 0, 0.0])
    noprice = 0
    for x in und:
        o = f(x.get('book_under_odds'))
        if o is None:
            noprice += 1
            continue
        b = ('+ dog' if o > 0 else '-100..-149' if o >= -149
             else '-150..-199' if o >= -199 else '-200..-299' if o >= -299
             else '-300 or worse')
        pay = (100 / -o) if o < 0 else (o / 100)
        a = buckets[b]
        if str(x.get('result')) == 'Win':
            a[0] += 1
            a[2] += pay
        else:
            a[1] += 1
            a[2] -= 1.0
    print(f'   unders with a price: {len(und)-noprice} of {len(und)} '
          f'({noprice} missing book_under_odds)')
    print(f'   {"price bucket":<16}{"W-L":>12}{"hit%":>8}{"units":>10}'
          f'{"ROI":>8}')
    for b in ('+ dog', '-100..-149', '-150..-199', '-200..-299',
              '-300 or worse'):
        w, l, u = buckets[b]
        if w + l == 0:
            continue
        print(f'   {b:<16}{f"{w}-{l}":>12}{100*w/(w+l):>7.1f}%{u:>10.2f}'
              f'{100*u/(w+l):>7.1f}%')
    tw = sum(v[0] for v in buckets.values())
    tl = sum(v[1] for v in buckets.values())
    tu = sum(v[2] for v in buckets.values())
    if tw + tl:
        print(f'   {"ALL PRICED UNDERS":<16}{f"{tw}-{tl}":>12}'
              f'{100*tw/(tw+tl):>7.1f}%{tu:>10.2f}{100*tu/(tw+tl):>7.1f}%')
    return 0


if __name__ == '__main__':
    sys.exit(main())
