#!/usr/bin/env python3
"""The one gate every new panelist passes through, or does not get a seat.

== 2026-10-14 - WHY THIS EXISTS ==
Three new panelists now write to panelist_predictions -- rolling_form,
pace_eff, goalie_special -- and each previously needed its own bespoke
measurement script. That is how a panelist gets seated on a number nobody
re-derived. This grades whatever is IN the table, the same way, every time.

It asks four questions in order, and a panelist has to survive all four:

 1. DOES IT DIFFERENTIATE?   prediction sd against the market's own sd. A
                             model whose predictions barely move is a
                             constant, and a constant's deviation from the
                             line is the LINE's level negated -- which
                             manufactures a slope out of nothing. Rolling form
                             scored sd 0.057 against market 0.382 and produced
                             a CI-clearing +0.567 that a literal constant
                             matched at +0.564.
 2. IS IT ACCURATE?          MAE against the market's MAE.
 3. ARE ITS DISAGREEMENTS    slope of the market's residual on our deviation,
    WORTH ANYTHING?          bootstrapped CI, AND the same slope for a
                             constant predictor. If the constant matches, the
                             verdict is CONSTANT ARTEFACT.
 4. DOES IT WIN MONEY?       the picks its number implies against the closing
                             line, graded at -110 with a Wilson CI.

MARGIN IS SKIPPED FOR MLB AND NHL. Their close_spread / close_puckline is a
fixed run/puck line of 2-5 distinct values, not a margin forecast; grading
against a near-constant measures nothing.

ONLY pregame=false rows are used here, and that is deliberate: the backfills
are walk-forward and leak-free, so they are valid for MEASURING an equation,
but they are not a live record because the equation was chosen knowing the
period. A live record needs pregame=true rows, which only accumulate forward.
The report says which it is reading.

USAGE
    python grade_panelist_predictions.py
    python grade_panelist_predictions.py --panelist goalie_special
    python grade_panelist_predictions.py --live
"""
from __future__ import annotations

import argparse
import math
import random
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
RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'NHL': 'nhl_game_results', 'NBA': 'nba_game_results',
           'MLB': 'mlb_game_results', 'NCAAB': 'ncaab_game_results'}
#: Sports whose close_spread is a REAL point spread.
REAL_SPREAD = ('NFL', 'NCAAF', 'NBA', 'NCAAB')
PRICE = -110
BREAKEVEN = 100 * abs(PRICE) / (abs(PRICE) + 100)
#: How far a panelist must disagree with the line to be making a pick.
EDGE_MIN = {'NFL': 1.0, 'NCAAF': 1.5, 'NBA': 1.5, 'NCAAB': 1.5,
            'MLB': 0.3, 'NHL': 0.25}
MIN_N = 40


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
        r = None
        for _ in range(3):
            try:
                r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                                 timeout=180)
                break
            except Exception:                                  # noqa: BLE001
                r = None
        if r is None or r.status_code not in (200, 206):
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def wilson(w, n):
    if n == 0:
        return None, None
    z, p = 1.96, w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def boot_slope(x, y, n=1200, seed=17):
    rnd = random.Random(seed)
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    k = len(xa)
    if k < 10:
        return None, None
    out = []
    for _ in range(n):
        idx = np.array([rnd.randrange(k) for _ in range(k)])
        xx, yy = xa[idx], ya[idx]
        if xx.std() == 0:
            continue
        out.append(float(np.polyfit(xx, yy, 1)[0]))
    if not out:
        return None, None
    out.sort()
    return out[int(0.025 * len(out))], out[int(0.975 * len(out))]


def market_and_outcome(sport, market, g):
    hs, as_ = _f(g.get('home_score')), _f(g.get('away_score'))
    if hs is None or as_ is None:
        return None, None
    if market == 'total':
        return _f(g.get('close_total')), hs + as_
    if sport not in REAL_SPREAD:
        return None, None
    cs = _f(g.get('close_spread'))
    if cs is None:
        return None, None
    home_line = -cs if sport == 'NFL' else cs
    return -home_line, hs - as_


#: *_game_context and *_game_results do not share an id space in every sport.
#: NFL context uses a 32-char hash while nfl_game_results uses
#: YYYYMMDD_AWAY_HOME, so a game_id join returns ZERO rows and 194 NFL
#: predictions silently graded as n=0 (project_nfl_game_id_mismatch_911).
#: panelist_predictions carries only the context id, so the fallback resolves
#: it through the context table to (date, home, away) -- the same composite key
#: build_panelist_records.joined() already relies on.
CONTEXT = {k: v.replace('_results', '_context') for k, v in RESULTS.items()}


def _ctx_key_map(sport):
    out = {}
    for c in page(CONTEXT[sport], {'select': 'game_id,game_date,home_team,'
                                             'away_team'}):
        out[str(c.get('game_id'))] = (str(c.get('game_date'))[:10],
                                      str(c.get('home_team')),
                                      str(c.get('away_team')))
    return out


def run(sport, panelist, market, preds, live):
    res_rows = page(RESULTS[sport], {'select': '*'})
    res = {str(r.get('game_id')): r for r in res_rows}
    by_key = {(str(r.get('game_date'))[:10], str(r.get('home_team')),
               str(r.get('away_team'))): r for r in res_rows}
    ctx_key = _ctx_key_map(sport)
    rows = []
    fell_back = 0
    for p in preds:
        gid = str(p.get('game_id'))
        g = res.get(gid)
        if not g:
            k = ctx_key.get(gid)
            if k:
                g = by_key.get(k)
            via_key = g is not None
        else:
            via_key = False
        if not g:
            continue
        mkt, y = market_and_outcome(sport, market, g)
        if mkt is None or y is None:
            continue
        pv = _f(p.get('prediction'))
        if pv is None:
            continue
        if via_key:
            fell_back += 1
        rows.append({'date': str(p.get('game_date'))[:10],
                     'p': pv, 'mkt': mkt, 'y': y})
    tag = 'pregame=true (LIVE RECORD)' if live else \
          'pregame=false (walk-forward backfill — valid to MEASURE, not a record)'
    print(f'\n{"="*80}\n{sport} · {panelist} · {market.upper()}   '
          f'n={len(rows)}   {tag}')
    if fell_back:
        print(f'  ({fell_back} of {len(rows)} graded rows joined via '
              f'(date, home, away) — {sport} context and results use '
              f'different id spaces)')
    if market == 'margin' and sport not in REAL_SPREAD:
        print('  SKIPPED — close_spread is a fixed run/puck line, not a '
              'margin forecast')
        return
    if len(rows) < MIN_N:
        print(f'  below MIN_N={MIN_N} — not reporting')
        return

    p = np.array([r['p'] for r in rows], float)
    mkt = np.array([r['mkt'] for r in rows], float)
    y = np.array([r['y'] for r in rows], float)
    const = float(np.mean(y))

    # 1. differentiation
    ratio = p.std() / mkt.std() if mkt.std() > 1e-9 else 0.0
    flag = '  ** ESSENTIALLY A CONSTANT **' if ratio < 0.10 else ''
    print(f'  1. differentiates  prediction sd {p.std():.3f} vs market sd '
          f'{mkt.std():.3f}  ratio {ratio:.2f}{flag}')
    # 2. accuracy
    print(f'  2. accuracy        model MAE {np.mean(np.abs(y-p)):.3f}  '
          f'market MAE {np.mean(np.abs(y-mkt)):.3f}  '
          f'constant({const:.2f}) MAE {np.mean(np.abs(y-const)):.3f}')
    # 3. informative deviation, with the constant control
    resid = y - mkt
    out = {}
    for lab, pp in (('model', p), ('constant', np.full(len(rows), const))):
        dev = pp - mkt
        if dev.std() < 1e-9:
            out[lab] = (None, None, None)
            continue
        sl = float(np.polyfit(dev, resid, 1)[0])
        lo, hi = boot_slope(dev, resid)
        out[lab] = (sl, lo, hi)
        print(f'  3. {lab:<9}      dev sd {dev.std():.3f}  slope {sl:+.3f}  '
              f'CI [{lo:+.3f}, {hi:+.3f}]' if lo is not None else
              f'  3. {lab:<9}      dev sd {dev.std():.3f}  slope {sl:+.3f}')
    msl, mlo, mhi = out.get('model', (None, None, None))
    csl, clo, chi = out.get('constant', (None, None, None))
    if msl is None or mlo is None:
        slope_verdict = 'no verdict'
    elif not (mlo > 0 or mhi < 0):
        slope_verdict = 'NOISE (CI includes zero)'
    elif clo is not None and not (mlo > chi or mhi < clo):
        slope_verdict = 'CONSTANT ARTEFACT (a constant scores the same slope)'
    elif mhi < 0:
        slope_verdict = 'BACKWARDS (CI below zero)'
    else:
        slope_verdict = 'INFORMATIVE (clears zero and clears the constant)'
    print(f'     -> {slope_verdict}')

    # 4. the picks its number implies
    emin = EDGE_MIN[sport]
    w = l = push = 0
    u = 0.0
    for r in rows:
        dev = r['p'] - r['mkt']
        if abs(dev) < emin:
            continue
        if abs(r['y'] - r['mkt']) < 1e-9:
            push += 1
            continue
        hit = (r['y'] > r['mkt']) == (dev > 0)
        if hit:
            w += 1
            u += 100 / abs(PRICE)
        else:
            l += 1
            u -= 1.0
    dec = w + l
    if dec == 0:
        print(f'  4. picks           none clear |disagreement| >= {emin}')
    else:
        hit = 100 * w / dec
        lo, hi = wilson(w, dec)
        print(f'  4. picks           {w}-{l}  {hit:.1f}%  CI [{lo:.1f},{hi:.1f}]'
              f'  {u:+.2f}u  ROI {100*u/dec:+.1f}%'
              f'{f"  ({push} push)" if push else ""}')
        print(f'     -> {"beats" if lo > BREAKEVEN else "does not beat"} '
              f'the {BREAKEVEN:.2f}% breakeven at 95%')

    seat = (slope_verdict.startswith('INFORMATIVE') and ratio >= 0.10)
    print(f'  SEAT: {"CANDIDATE — now check independence" if seat else "NO"}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--panelist')
    ap.add_argument('--sport', choices=sorted(RESULTS))
    ap.add_argument('--live', action='store_true',
                    help='grade pregame=true rows (the live record) instead '
                         'of the walk-forward backfill')
    a = ap.parse_args()
    params = {'select': '*', 'pregame': f'is.{str(a.live).lower()}'}
    if a.panelist:
        params['panelist'] = f'eq.{a.panelist}'
    if a.sport:
        params['sport'] = f'eq.{a.sport}'
    preds = page('panelist_predictions', params)
    print('PANELIST GATE — differentiate, be accurate, disagree usefully, '
          'beat a constant, win money.')
    print(f'{len(preds)} prediction rows read from panelist_predictions')
    if not preds:
        print('nothing to grade')
        return 0
    groups = defaultdict(list)
    for p in preds:
        groups[(p.get('sport'), p.get('panelist'), p.get('market'))].append(p)
    for (sport, panelist, market) in sorted(groups):
        if sport not in RESULTS:
            continue
        try:
            run(sport, panelist, market, groups[(sport, panelist, market)],
                a.live)
        except Exception as e:                                 # noqa: BLE001
            print(f'\n{sport} {panelist} {market}: FAILED '
                  f'{type(e).__name__}: {str(e)[:120]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
