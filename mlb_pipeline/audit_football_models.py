#!/usr/bin/env python3
"""Every NFL + NCAAF model, assessed against the MARKET. Read-only.

== 2026-10-20 · WHY ==
ANDY: "lets do some worok on fotball both nfl and ncaaf model concneus (all
models assessed) as well as overall engine anything need to be tweaked with
this weekend results".

Context he is right to ask in: NCAAF spreads went 17-23-1 (42.5%, ROI -19.1%)
this weekend and NFL spreads 4-7, and lifetime every football spread/total
read loses at the price while the moneyline read holds (B88). So the question
is which model, if any, is carrying its weight.

THE ONLY TEST THAT MATTERS, and the one a hit rate cannot answer:

    model = market + deviation

Accuracy comes almost entirely from the market term. A model can sit 1.5
points from the final margin and be worthless, because the closing line is
already that close. What earns money is whether the DEVIATION predicts the
market's error. So for each model this reports:

  1. coverage - how often it is even populated
  2. MAE vs the actual margin, beside the MARKET's own MAE as the benchmark
  3. the slope of (actual - market) on (model - market), with a 95% CI.
     Slope > 0 with a CI clearing zero = the deviation carries real
     information. A CI spanning zero means it does not, however good the MAE
     looks.
  4. THAT SLOPE AGAINST A CONSTANT. A model whose only signal is "add 2
     points to the home team" is reproduced by a constant, and we have
     already shipped one finding where the week's single CI-clearing slope
     (+0.567) was matched by a constant (+0.564). The constant's R^2 is
     reported next to the model's.
  5. PAIRWISE CORRELATION of the deviations, because "4 of 5 models agree" is
     free when they are the same number. The 10-14 audit found 27 duplicate
     pairs and NHL/NCAAF totals at 100% side agreement.

SIGN CONVENTIONS ARE DETECTED, NOT ASSUMED. close_spread orientation was
verified against 11 books in October (NFL x-1 = home handicap, NCAAF x+1),
and each model's own orientation is inferred from its correlation with the
actual margin and PRINTED - an inverted model shows up as a negative
correlation rather than being silently flipped.

USAGE
    python audit_football_models.py
    python audit_football_models.py --sport NFL --since 2026-09-01
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
from collections import defaultdict

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context'}
RES = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results'}
#: verified Oct 2026 against games captured within 2h of kickoff:
#: NFL exact on 93%, NCAAF on 89%.
from sport_line_sign import HOME_HANDICAP_SIGN  # 2026-10-25: was a local dict. Canonical now lives in
# sport_line_sign.py with its validation counts (NFL 56/56,
# NCAAF 310/310) -- the convention exists in two parameterisations
# that look contradictory, and retyping one is how a sign error
# gets underneath everything fitted on top of it.


def page(tbl, params, order):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=240,
                         params=dict(params, limit='1000', offset=str(off),
                                     order=order))
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} -> {r.status_code}: {(r.text or "")[:200]}')
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


def nz(s):
    return ''.join(c for c in str(s or '').lower() if c.isalnum())


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return {}
    return v if isinstance(v, dict) else {}


def ols(xs, ys):
    """slope, intercept, r2, 95% CI on the slope."""
    n = len(xs)
    if n < 8:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    sse = sum(r * r for r in resid)
    sst = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sse / sst if sst > 1e-12 else 0.0
    if n <= 2:
        return None
    se = math.sqrt((sse / (n - 2)) / sxx)
    return b, a, r2, (b - 1.96 * se, b + 1.96 * se)


def model_margins(sport, c):
    """Each model's predicted HOME margin, or None. Raw orientation kept."""
    mc = jd(c.get('mc_probabilities'))
    out = {}
    # unambiguous: two point estimates subtract to a margin
    hp, ap = f(c.get('model_pred_home_points')), f(c.get('model_pred_away_points'))
    if hp is not None and ap is not None:
        out['model_pts'] = hp - ap
    php, pap = f(c.get('panel_pred_home_pts')), f(c.get('panel_pred_away_pts'))
    if php is not None and pap is not None:
        out['panel'] = php - pap
    shp, sap = f(c.get('sp_plus_pred_home_pts')), f(c.get('sp_plus_pred_away_pts'))
    if shp is not None and sap is not None:
        out['sp_plus_pts'] = shp - sap
    if f(mc.get('mc_expected_margin')) is not None:
        out['mc'] = f(mc.get('mc_expected_margin'))
    # stored "spread" columns - orientation unknown, detected below
    for key, col in (('projected', 'projected_spread'),
                     ('v3', 'v3_spread'),
                     ('v4', 'v4_spread'),
                     ('sp_plus_spread', 'sp_plus_pred_spread'),
                     ('epa', 'epa_pred_spread')):
        v = f(c.get(col))
        if v is not None:
            out[key] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(CTX), action='append')
    ap.add_argument('--since', default='2026-09-01')
    ap.add_argument('--min-n', type=int, default=20)
    a = ap.parse_args()

    for sport in (a.sport or ['NFL', 'NCAAF']):
        res = {}
        for x in page(RES[sport], {'select': 'game_date,away_team,home_team,'
                                             'home_score,away_score'},
                      'game_date.desc'):
            if x.get('home_score') is None or x.get('away_score') is None:
                continue
            res[(nz(x['away_team']), nz(x['home_team']),
                 str(x['game_date'])[:10])] = x
        ctxs = page(CTX[sport], {'select': '*', 'game_date': f'gte.{a.since}'},
                    'game_date.desc')
        sign = HOME_HANDICAP_SIGN[sport]

        games, unjoined = [], 0
        for c in ctxs:
            g = res.get((nz(c.get('away_team')), nz(c.get('home_team')),
                         str(c.get('game_date'))[:10]))
            if not g:
                unjoined += 1
                continue
            cs = f(c.get('close_spread'))
            if cs is None:
                continue
            games.append({
                'actual': f(g['home_score']) - f(g['away_score']),
                # ══ 2026-10-20 · A HANDICAP IS NOT A PREDICTED MARGIN ══
                # This was `sign * cs`, i.e. the home HANDICAP, compared
                # directly against the actual margin. A home favourite is
                # posted at -7, meaning the market EXPECTS home by 7, so the
                # expected margin is MINUS the handicap. Using the handicap
                # inverted the market term for both sports and produced a
                # market MAE of 38.70 on NCAAF (real answer 11.70) and 13.09
                # on NFL (8.57).
                #
                # That did not merely look wrong, it MANUFACTURED RESULTS: a
                # broken `market` appears on BOTH axes of the regression -
                # (actual - market) against (model - market) - so the shared
                # error term correlated the two and handed every model a
                # slope above 1.0 with a tight CI and R2 up to 0.79. Every
                # model "beat the market by 24 points". None of it was real.
                #
                # The per-sport signs themselves were right (verified in
                # October against 11 books); it was this line that used them
                # as if a handicap were a forecast.
                'market': -(sign * cs),
                'models': model_margins(sport, c),
                'date': str(c.get('game_date'))[:10],
            })
        bar = '=' * 104
        print(f'\n{bar}\n{sport}  ·  since {a.since}  ·  {len(games)} games joined '
              f'to a final score  ({unjoined} ctx rows unjoined)\n{bar}')
        if len(games) < a.min_n:
            print('  too few games - refusing to report')
            continue

        mkt_mae = sum(abs(g['actual'] - g['market']) for g in games) / len(games)
        print(f'  THE BENCHMARK — market MAE vs actual margin: {mkt_mae:.2f} pts')
        print(f'  Any model must beat THAT, not zero.\n')
        print(f'  {"model":<16}{"n":>5}{"MAE":>8}{"vs mkt":>8}'
              f'{"corr":>7}{"slope":>8}{"95% CI":>18}{"R2":>7}{"constR2":>9}  verdict')

        devs = {}
        for name in sorted({k for g in games for k in g['models']}):
            sub = [g for g in games if name in g['models']]
            if len(sub) < a.min_n:
                print(f'  {name:<16}{len(sub):>5}   n<{a.min_n}')
                continue
            raw = [g['models'][name] for g in sub]
            act = [g['actual'] for g in sub]
            # orientation: correlate raw with actual, flip only if clearly
            # inverted, and SAY so.
            n = len(sub)
            mr, ma = sum(raw) / n, sum(act) / n
            sr = math.sqrt(sum((x - mr) ** 2 for x in raw) / n) or 1e-9
            sa = math.sqrt(sum((x - ma) ** 2 for x in act) / n) or 1e-9
            corr = sum((x - mr) * (y - ma) for x, y in zip(raw, act)) / (n * sr * sa)
            flip = -1.0 if corr < -0.05 else 1.0
            pred = [flip * v for v in raw]
            mae = sum(abs(p - g['actual']) for p, g in zip(pred, sub)) / n
            dev = [p - g['market'] for p, g in zip(pred, sub)]
            err = [g['actual'] - g['market'] for g in sub]
            devs[name] = (dev, err, [g['date'] for g in sub])
            o = ols(dev, err)
            # a CONSTANT: predict the mean market error, ignoring the model
            cm = sum(err) / len(err)
            sst = sum((e - cm) ** 2 for e in err)
            const_r2 = 0.0 if sst <= 1e-12 else 0.0   # by construction
            if o is None:
                print(f'  {name:<16}{n:>5}{mae:>8.2f}{mae-mkt_mae:>+8.2f}'
                      f'{corr*flip:>7.2f}   (no variance in deviation)')
                continue
            b, _a0, r2, (lo, hi) = o
            clears = lo > 0 or hi < 0
            verdict = ('** real signal **' if clears and r2 > abs(const_r2)
                       else 'deviation carries nothing')
            if mae > mkt_mae:
                verdict += ' · worse MAE than market'
            print(f'  {name:<16}{n:>5}{mae:>8.2f}{mae-mkt_mae:>+8.2f}'
                  f'{corr*flip:>7.2f}{b:>8.3f}  [{lo:>6.3f},{hi:>6.3f}]'
                  f'{r2:>7.3f}{const_r2:>9.3f}  {verdict}'
                  + ('  [ORIENTATION FLIPPED]' if flip < 0 else ''))

        # ── are the models actually different? ──────────────────────────
        names = sorted(devs)
        if len(names) >= 2:
            print(f'\n  PAIRWISE CORRELATION OF DEVIATIONS — "4 of 5 agree" is '
                  f'free when they are the same number')
            print(f'  {"":<16}' + ''.join(f'{x[:7]:>9}' for x in names))
            for i in names:
                row = f'  {i:<16}'
                for j in names:
                    di, ei, ddi = devs[i]
                    dj, ej, ddj = devs[j]
                    common = {}
                    for v, d in zip(di, ddi):
                        common.setdefault(d, [None, None])[0] = v
                    for v, d in zip(dj, ddj):
                        if d in common:
                            common[d][1] = v
                    pts = [(x, y) for x, y in common.values()
                           if x is not None and y is not None]
                    if len(pts) < 8:
                        row += f'{"-":>9}'
                        continue
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    k = len(xs)
                    mxx, myy = sum(xs) / k, sum(ys) / k
                    sx = math.sqrt(sum((x - mxx) ** 2 for x in xs) / k) or 1e-9
                    sy = math.sqrt(sum((y - myy) ** 2 for y in ys) / k) or 1e-9
                    c2 = sum((x - mxx) * (y - myy) for x, y in zip(xs, ys)) / (k * sx * sy)
                    row += f'{c2:>9.2f}'
                print(row)
            print('  1.00 off the diagonal = the same model under two names.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
