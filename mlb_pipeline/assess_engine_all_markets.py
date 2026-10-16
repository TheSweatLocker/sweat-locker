#!/usr/bin/env python3
"""The engine across EVERY market: ATS, moneyline and totals, all sports.

== 2026-10-16 - WHY ==
ANDY: "I also want evaluate ATS not just sides, the general engine selection
process including spreads and totals".

assess_football_engine.py covered NFL/NCAAF sides. This covers all three
markets in every sport that has them, graded in the market each pick actually
settles in -- which is the error that produced a fake NCAAF ml:STRONG of 31.8%
when the true figure is 50.0% (a moneyline bet is settled by who WON, not who
covered).

SAME DISCIPLINE THROUGHOUT:
  * point-in-time context fields only; nothing from a current-state helper
  * temporal split, TRAIN printed as a ceiling, TEST is the result
  * graded in UNITS: ATS and totals at -110, moneyline at the real de-vigged price
  * free controls per market -- always HOME / AWAY / FAVOURITE for sides,
    always OVER / UNDER for totals. A number beats nothing unless it beats these.
  * Wilson CI and n beside every percentage
  * a sign assertion per sport that refuses to print if the line does not
    correlate with the outcome

THREE QUESTIONS PER MARKET:
  A. is our number on the market's SCALE, and is it more accurate?
  B. is our DEVIATION from the market informative? (model = market + deviation)
  C. what did the PUBLISHED pick do, by tier?

USAGE
    python assess_engine_all_markets.py
    python assess_engine_all_markets.py --sport NCAAF
"""
from __future__ import annotations

import argparse
import json
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
TRAIN_FRAC = 0.60
PRICE = -110
BREAKEVEN = 100 * abs(PRICE) / (abs(PRICE) + 100)

SPORTS = {
    'NFL':   ('nfl_game_context', 'nfl_game_results', True),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results', True),
    'NHL':   ('nhl_game_context', 'nhl_game_results', False),
    'MLB':   ('mlb_game_context', 'mlb_game_results', False),
    'NBA':   ('nba_game_context', 'nba_game_results', True),
}
#: Sports whose close_spread is a real point spread. MLB/NHL run a fixed
#: run/puck line, so ATS is not a market we can grade there.
REAL_SPREAD = ('NFL', 'NCAAF', 'NBA', 'NCAAB')


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


def boot_slope(x, y, n=800, seed=17):
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


def flat(label, picks, indent='    ', tag=''):
    """picks: (took_high_side: bool, line: float, actual: float) at -110.
    took_high_side means HOME on a spread, OVER on a total."""
    w = l = p = 0
    u = 0.0
    for th, line, act in picks:
        e = act - line
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
          + (f'  ({p} push)' if p else '') + (f'  {tag}' if tag else ''))
    return 100 * u / n


def ml_grade(label, picks, indent='    '):
    """picks: (took_home, home_ml, away_ml, actual_home_margin)."""
    w = l = 0
    u = 0.0
    for th, hml, aml, mg in picks:
        price = hml if th else aml
        if price is None or mg == 0:
            continue
        pay = (100 / -price) if price < 0 else (price / 100)
        if ((mg > 0) if th else (mg < 0)):
            w += 1
            u += pay
        else:
            l += 1
            u -= 1.0
    n = w + l
    if n < 12:
        print(f'{indent}{label:<34}n={n} too few')
        return None
    lo, hi = wilson(w, n)
    print(f'{indent}{label:<34}{w}-{l}  {100*w/n:5.1f}%  CI[{lo:.1f},{hi:.1f}]'
          f'  {u:+7.2f}u  ROI {100*u/n:+6.1f}%  n={n}  [ML]')
    return 100 * u / n


def scale_block(label, ours, mkt, actual, indent='    '):
    if len(ours) < 20:
        print(f'{indent}{label}: n={len(ours)} too few')
        return
    o, m, a = np.array(ours), np.array(mkt), np.array(actual)
    print(f'{indent}{label}: n={len(o)}  our sd {o.std():.3f}  '
          f'market sd {m.std():.3f}  ratio {o.std()/max(m.std(),1e-9):.2f}')
    print(f'{indent}  MAE ours {np.mean(np.abs(a-o)):.3f}  '
          f'market {np.mean(np.abs(a-m)):.3f}'
          + ('  (we are WORSE)' if np.mean(np.abs(a-o)) >
             np.mean(np.abs(a-m)) else '  (we are better)'))
    dev, resid = o - m, a - m
    if dev.std() > 1e-9:
        sl = float(np.polyfit(dev, resid, 1)[0])
        lo, hi = boot_slope(dev, resid)
        cis = f'[{lo:+.3f}, {hi:+.3f}]' if lo is not None else 'n/a'
        v = ('INFORMATIVE' if lo is not None and lo > 0
             else 'BACKWARDS' if hi is not None and hi < 0 else 'noise')
        print(f'{indent}  deviation slope {sl:+.3f}  CI {cis}  -> {v}')


def run(sport):
    ctxt, rest, has_spread = SPORTS[sport]
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
        if hs is None or as_ is None:
            continue
        cs = f(c.get('close_spread'))
        mkt_sp = None
        if cs is not None and sport in REAL_SPREAD:
            mkt_sp = cs if sport == 'NFL' else -cs
        rows.append({'c': c, 'mg': hs - as_, 'tot': hs + as_,
                     'sp': mkt_sp, 'ct': f(c.get('close_total')),
                     'hml': f(c.get('close_home_ml') or c.get('home_ml')),
                     'aml': f(c.get('close_away_ml') or c.get('away_ml')),
                     'date': str(c.get('game_date'))[:10]})
    rows.sort(key=lambda r: r['date'])
    print('=' * 78)
    print(f'{sport}   {len(rows)} graded games')
    print('=' * 78)
    if len(rows) < 40:
        print('  too few to assess\n')
        return
    cut = int(len(rows) * TRAIN_FRAC)
    tr, te = rows[:cut], rows[cut:]
    print(f'temporal split at {rows[cut]["date"]}: train {len(tr)}  '
          f'test {len(te)}')

    # ---------------- SPREAD / ATS ----------------
    sp = [r for r in te if r['sp'] is not None]
    if len(sp) >= 20:
        mk = np.array([r['sp'] for r in sp])
        mg = np.array([r['mg'] for r in sp])
        corr = float(np.corrcoef(mk, mg)[0, 1])
        cov = float(((mg - mk) > 0).mean())
        print(f'\n  ── ATS / SPREAD ──  sign check corr {corr:+.3f}, '
              f'always-HOME {100*cov:.1f}%')
        if corr < 0.3 or not (0.30 < cov < 0.70):
            print('    ** sign looks wrong — skipping ATS **')
        else:
            o = [f(r['c'].get('projected_spread')) for r in sp]
            pr = [(a, r) for a, r in zip(o, sp) if a is not None]
            if pr:
                scale_block('projected_spread',
                            [a for a, _ in pr],
                            [r['sp'] for _, r in pr],
                            [r['mg'] for _, r in pr])
            flat('control: always HOME',
                 [(True, r['sp'], r['mg']) for r in sp])
            flat('control: always AWAY',
                 [(False, r['sp'], r['mg']) for r in sp])
            flat('control: always FAVOURITE',
                 [(r['sp'] > 0, r['sp'], r['mg']) for r in sp])
            byt = defaultdict(list)
            allp = []
            for r in sp:
                pp = jd(r['c'].get('primary_play'))
                side = str(pp.get('side') or '').upper()
                if (side not in ('HOME', 'AWAY')
                        or str(pp.get('type') or '').lower() == 'ml'):
                    continue
                t = (side == 'HOME', r['sp'], r['mg'])
                allp.append(t)
                byt[f"{pp.get('type')}:{pp.get('tier')}"].append(t)
            flat('PUBLISHED spread, all tiers', allp)
            for k in sorted(byt, key=lambda x: -len(byt[x])):
                flat(f'  {k}', byt[k])

    # ---------------- MONEYLINE ----------------
    mlr = [r for r in te if r['hml'] is not None and r['aml'] is not None]
    if len(mlr) >= 20:
        print(f'\n  ── MONEYLINE ──  n={len(mlr)} with a two-way price')
        ml_grade('control: always HOME',
                 [(True, r['hml'], r['aml'], r['mg']) for r in mlr])
        # The favourite is the MORE NEGATIVE price. An earlier version used
        # hml >= aml, which backs the DOG and scored 25.6% — a broken control
        # is worse than no control, because it makes everything look good.
        ml_grade('control: market favourite',
                 [(r['hml'] < r['aml'], r['hml'], r['aml'], r['mg'])
                  for r in mlr])
        ml_grade('control: market underdog',
                 [(r['hml'] > r['aml'], r['hml'], r['aml'], r['mg'])
                  for r in mlr])
        byt = defaultdict(list)
        allp = []
        for r in mlr:
            pp = jd(r['c'].get('primary_play'))
            side = str(pp.get('side') or '').upper()
            if (side not in ('HOME', 'AWAY')
                    or str(pp.get('type') or '').lower() != 'ml'):
                continue
            t = (side == 'HOME', r['hml'], r['aml'], r['mg'])
            allp.append(t)
            byt[f"ml:{pp.get('tier')}"].append(t)
        ml_grade('PUBLISHED ML, all tiers', allp)
        for k in sorted(byt, key=lambda x: -len(byt[x])):
            ml_grade(f'  {k}', byt[k])

    # ---------------- TOTALS ----------------
    tt = [r for r in te if r['ct'] is not None]
    if len(tt) >= 20:
        ac = np.array([r['tot'] for r in tt])
        cl = np.array([r['ct'] for r in tt])
        over_rate = float(((ac - cl) > 0).mean())
        print(f'\n  ── TOTALS ──  n={len(tt)}  always-OVER hits '
              f'{100*over_rate:.1f}%')
        o = [f(r['c'].get('projected_total')) for r in tt]
        pr = [(a, r) for a, r in zip(o, tt) if a is not None]
        if pr:
            scale_block('projected_total',
                        [a for a, _ in pr],
                        [r['ct'] for _, r in pr],
                        [r['tot'] for _, r in pr])
        flat('control: always OVER',
             [(True, r['ct'], r['tot']) for r in tt])
        flat('control: always UNDER',
             [(False, r['ct'], r['tot']) for r in tt])
        # the model's own total lean
        lean = [(a > r['ct'], r['ct'], r['tot']) for a, r in pr]
        flat('follow projected_total lean', lean)
        # published total picks live on jerry_reads, not primary_play
        jr = page('jerry_reads', {'select': '*', 'sport': f'eq.{sport}',
                                  'call_market': 'eq.total'})
        gm = {(str(x.get('game_date'))[:10], str(x.get('game_id'))): x
              for x in jr}
        jp, jt = [], defaultdict(list)
        for r in tt:
            k = (r['date'], str(r['c'].get('game_id')))
            x = gm.get(k)
            if not x:
                continue
            side = str(x.get('call_side') or '').upper()
            if side not in ('OVER', 'UNDER'):
                continue
            t = (side == 'OVER', r['ct'], r['tot'])
            jp.append(t)
            cv = f(x.get('conviction'))
            band = ('conv 70+' if cv is not None and cv >= 70
                    else 'conv 60-69' if cv is not None and cv >= 60
                    else 'conv <60' if cv is not None else 'conv n/a')
            jt[band].append(t)
        flat('PUBLISHED totals (jerry_reads)', jp)
        for k in sorted(jt, key=lambda x: -len(jt[x])):
            flat(f'  {k}', jt[k])
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(SPORTS))
    a = ap.parse_args()
    print('ENGINE ASSESSMENT — ALL MARKETS. Point-in-time fields, temporal '
          'split, real prices, free controls.')
    print(f'ATS and totals at {PRICE} (breakeven {BREAKEVEN:.2f}%); '
          f'moneyline at its own price. TEST slice only.\n')
    for s in ([a.sport] if a.sport else ['NCAAF', 'NFL', 'NHL', 'MLB']):
        try:
            run(s)
        except Exception as e:                                 # noqa: BLE001
            print(f'{s}: FAILED {type(e).__name__}: {str(e)[:140]}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
