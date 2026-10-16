#!/usr/bin/env python3
"""The clean NFL + NCAAF engine assessment. Temporal split, real prices, controls.

== 2026-10-16 - WHY ==
ANDY: "i still dont know the actual stats of nfl engine process and model
calculation because of so many leaks everyday and misinformation im over it."

Fair. Every number below is built the one way that has survived this week:

 1. POINT-IN-TIME ONLY. Fields read off the context row, which is written
    pre-game. Nothing recomputed from a current-state helper — that is what
    broke assess_v4_on_2026.py, where build_current_epa() returns each team's
    LATEST week and scored September games with October form.
 2. TEMPORAL SPLIT. Earlier 60% is TRAIN and is printed as the overfit
    ceiling. Only the later 40% is reported as a result.
 3. REAL PRICES, IN UNITS. ATS at -110 for spread markets; the actual
    moneyline, de-vigged, for ML. A hit rate without a price is not a result.
 4. CONTROLS. always-HOME, always-AWAY, always-FAVOURITE. A number beats
    nothing unless it beats these — home-field and favourite-backing are free.
 5. WILSON CI and n beside every percentage.

THE THREE QUESTIONS, per sport:
  A. Is the projection on the market's SCALE, and is it accurate?
  B. Is the DEVIATION from the market informative? (model = market + deviation;
     accuracy comes from the market term, profit only from the deviation)
  C. What did the PUBLISHED pick actually do, by tier?

USAGE
    python assess_football_engine.py
    python assess_football_engine.py --sport NFL
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict

import numpy as np
import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
TRAIN_FRAC = 0.60
PRICE = -110
BREAKEVEN = 100 * abs(PRICE) / (abs(PRICE) + 100)
SPEC = {'NFL': ('nfl_game_context', 'nfl_game_results'),
        'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results')}


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


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                      # noqa: BLE001
            return {}
    return v if isinstance(v, dict) else {}


def wilson(w, n):
    if n == 0:
        return None, None
    z, p = 1.96, w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def boot_slope(x, y, n=1000, seed=17):
    rnd = random.Random(seed)
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    k = len(xa)
    if k < 12:
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


def ats(label, picks, indent='    '):
    """picks: list of (took_home, market_home_margin, actual_margin)."""
    w = l = p = 0
    u = 0.0
    for th, cs, mg in picks:
        e = mg - cs
        if abs(e) < 1e-9:
            p += 1
            continue
        if (e > 0) == th:
            w += 1
            u += 100 / abs(PRICE)
        else:
            l += 1
            u -= 1.0
    n = w + l
    if n < 12:
        print(f'{indent}{label:<34}n={n} too few')
        return None
    lo, hi = wilson(w, n)
    print(f'{indent}{label:<34}{w}-{l}  {100*w/n:5.1f}%  CI[{lo:.1f},{hi:.1f}]'
          f'  {u:+7.2f}u  ROI {100*u/n:+6.1f}%  n={n}'
          + (f'  ({p} push)' if p else ''))
    return 100 * u / n


def run(sport):
    ctxt, rest = SPEC[sport]
    ctx = page(ctxt, {'select': '*'})
    res = page(rest, {'select': '*'})
    by_id = {str(r.get('game_id')): r for r in res}
    by_key = {(str(r.get('game_date'))[:10], str(r.get('home_team')),
               str(r.get('away_team'))): r for r in res}
    rows = []
    for c in ctx:
        if c.get('stats_source') == 'preseason':
            continue
        g = (by_id.get(str(c.get('game_id')))
             or by_key.get((str(c.get('game_date'))[:10],
                            str(c.get('home_team')), str(c.get('away_team')))))
        hs, as_ = f((g or {}).get('home_score')), f((g or {}).get('away_score'))
        cs = f(c.get('close_spread'))
        if hs is None or as_ is None or cs is None:
            continue
        # close_spread is the AWAY line on NFL, the HOME handicap elsewhere
        mkt = cs if sport == 'NFL' else -cs
        rows.append({'c': c, 'mkt': mkt, 'mg': hs - as_,
                     'tot': hs + as_, 'ct': f(c.get('close_total')),
                     'date': str(c.get('game_date'))[:10]})
    rows.sort(key=lambda r: r['date'])
    print('=' * 78)
    print(f'{sport}   {len(rows)} graded games with a closing spread')
    print('=' * 78)
    if len(rows) < 40:
        print('too few')
        return
    cut = int(len(rows) * TRAIN_FRAC)
    tr, te = rows[:cut], rows[cut:]
    print(f'temporal split at {rows[cut]["date"]}: train {len(tr)}  '
          f'test {len(te)}')

    mk = np.array([r['mkt'] for r in rows])
    mg = np.array([r['mg'] for r in rows])
    corr = float(np.corrcoef(mk, mg)[0, 1])
    cov = float(((mg - mk) > 0).mean())
    print(f'SIGN CHECK  corr(line, margin) {corr:+.3f}  '
          f'always-HOME ATS {100*cov:.1f}%')
    if corr < 0.3 or not (0.35 < cov < 0.65):
        print('  ** SIGN LOOKS WRONG — refusing to report numbers **')
        return

    # A. scale + accuracy of the projection, on TEST
    print('\n  A. PROJECTION vs MARKET (test slice)')
    for col, lbl in (('projected_spread', 'projected_spread'),):
        v = [f(r['c'].get(col)) for r in te]
        pairs = [(a, r) for a, r in zip(v, te) if a is not None]
        if len(pairs) < 20:
            print(f'    {lbl}: n={len(pairs)} too few')
            continue
        pv = np.array([a for a, _ in pairs])
        pm = np.array([r['mkt'] for _, r in pairs])
        pg = np.array([r['mg'] for _, r in pairs])
        print(f'    n={len(pairs)}  our sd {pv.std():.3f}  market sd '
              f'{pm.std():.3f}  ratio {pv.std()/max(pm.std(),1e-9):.2f}')
        print(f'    MAE  ours {np.mean(np.abs(pg-pv)):.3f}   '
              f'market {np.mean(np.abs(pg-pm)):.3f}')
        dev = pv - pm
        resid = pg - pm
        if dev.std() > 1e-9:
            sl = float(np.polyfit(dev, resid, 1)[0])
            lo, hi = boot_slope(dev, resid)
            cis = f'[{lo:+.3f}, {hi:+.3f}]' if lo is not None else 'n/a'
            v_ = ('INFORMATIVE' if lo is not None and lo > 0
                  else 'BACKWARDS' if hi is not None and hi < 0 else 'noise')
            print(f'    B. deviation slope {sl:+.3f}  CI {cis}  -> {v_}')

    # controls + published, on TEST
    print('\n  CONTROLS (test slice)')
    ats('always HOME', [(True, r['mkt'], r['mg']) for r in te])
    ats('always AWAY', [(False, r['mkt'], r['mg']) for r in te])
    ats('always FAVOURITE',
        [(r['mkt'] > 0, r['mkt'], r['mg']) for r in te])

    print('\n  C. PUBLISHED PICK (test slice)')
    allp, bytier = [], defaultdict(list)
    for r in te:
        pp = jd(r['c'].get('primary_play'))
        side = str(pp.get('side') or '').upper()
        if side not in ('HOME', 'AWAY'):
            continue
        t = (True, r['mkt'], r['mg']) if side == 'HOME' else \
            (False, r['mkt'], r['mg'])
        allp.append(t)
        bytier[f"{pp.get('type')}:{pp.get('tier')}"].append(t)
    ats('published, all tiers', allp)
    for k in sorted(bytier, key=lambda x: -len(bytier[x])):
        ats(f'  {k}', bytier[k])

    # model-vs-market selector, the NHL lesson applied to football
    print('\n  D. SELECT ON ABSOLUTE PROJECTION vs ON DISAGREEMENT')
    pv = {id(r): f(r['c'].get('projected_spread')) for r in rows}
    trd = [abs(pv[id(r)] - r['mkt']) for r in tr if pv[id(r)] is not None]
    if trd:
        thr = float(np.median(trd))
        big = [(pv[id(r)] > r['mkt'], r['mkt'], r['mg']) for r in te
               if pv[id(r)] is not None
               and abs(pv[id(r)] - r['mkt']) > thr]
        small = [(pv[id(r)] > r['mkt'], r['mkt'], r['mg']) for r in te
                 if pv[id(r)] is not None
                 and abs(pv[id(r)] - r['mkt']) <= thr]
        print(f'    (train median |deviation| = {thr:.2f})')
        ats('follow model, BIG disagreement', big)
        ats('follow model, small disagreement', small)
        ats('FADE model, BIG disagreement',
            [(not a, b, c) for a, b, c in big])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(SPEC))
    a = ap.parse_args()
    print('FOOTBALL ENGINE ASSESSMENT — point-in-time fields, temporal split, '
          'real prices, controls.')
    print(f'ATS graded at {PRICE} (breakeven {BREAKEVEN:.2f}%). '
          f'TRAIN is a ceiling, TEST is the result.\n')
    for s in ([a.sport] if a.sport else ['NFL', 'NCAAF']):
        run(s)
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
