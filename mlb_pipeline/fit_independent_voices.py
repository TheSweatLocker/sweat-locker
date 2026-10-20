#!/usr/bin/env python3
"""Fit a PANEL of genuinely independent model voices, per sport. Read-only.

== 2026-10-20 · WHY, IN ANDY'S WORDS ==
"I want eavh model to wrok as an indepenedtent opinion in the sense that
npopne model can says KC by 10 while another says KC by 3 or another says what
ever team KC plays covers the 10. All models should be different opiion derived
from real data or angles that are fitting to the output. As if it was a panel
of broadcaster with opinions based on real data, maybe model X favors
homefield advantage over model Y. I am talking about the models themselve
espexillay if they dupilates of each right now as you say. THis shoudl eb for
evey sport"

He is right that this was never fixed. Twice I hid a duplicate tile and once I
labelled one; none of that adds an opinion. Measured today:

    NCAAF  proj_raw  == sp_plus       282/282  (100%)
           model_pts == sp_plus_pts   272/272  (100%)
    NFL    proj_raw  == proj_v2        91/91   (100%)
           projected == v3             57/106  ( 54%)
    NHL    mc  within 0.25 of projected 187/187 (100%)  - ONE voice
    NBA    two model fields in total

So several sports do not have a panel at all.

WHAT A VOICE HAS TO BE, to count as one here:
  * built from its OWN data family, not a relabelling of another's output
  * converted to points by coefficients FITTED ON HISTORY, not invented -
    each voice's slope and intercept come from regressing the actual margin
    on that voice's own raw gap, so the intercept IS its home-field stance
    and two voices can legitimately weight home differently
  * reported with its MAE against the market's MAE, so a voice that is just
    noise is visible as noise
  * reported with its correlation to every OTHER voice, because a voice that
    duplicates another is not a second opinion no matter what it is called

This script only FITS and REPORTS. It writes nothing. The coefficients it
prints are what a production writer would use.

USAGE
    python fit_independent_voices.py
    python fit_independent_voices.py --sport NHL
"""
from __future__ import annotations
import argparse
import itertools
import json
import math
import os
import sys

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

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'NHL': 'nhl_game_context', 'NBA': 'nba_game_context'}
RES = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
       'NHL': 'nhl_game_results', 'NBA': 'nba_game_results'}
#: home handicap sign, verified Oct 2026 against 11 books.
#: expected margin = -(sign * close_spread)
HCAP_SIGN = {'NFL': -1.0, 'NCAAF': 1.0, 'NHL': 1.0, 'NBA': 1.0}


def page(tbl, params, order):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=240,
                         params=dict(params, limit='1000', offset=str(off),
                                     order=order))
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} -> {r.status_code}: {(r.text or "")[:160]}')
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


def fit(xs, ys):
    """slope, intercept, r, mae of the fitted line."""
    n = len(xs)
    if n < 20:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-9:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / n) or 1e-9
    sx = math.sqrt(sxx / n) or 1e-9
    r = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n * sx * sy)
    mae = sum(abs(y - (a + b * x)) for x, y in zip(xs, ys)) / n
    return b, a, r, mae, n


#: Each voice is (name, angle, extractor -> raw home-minus-away gap).
#: The ANGLE is the editorial stance - what this broadcaster actually argues
#: from. Two voices may use the same sport but never the same family.
VOICES = {
    'NHL': [
        ('ELO', 'pure strength rating, ignores how goals are made',
         lambda c, m: (f(c.get('elo_home')) - f(c.get('elo_away')))
         if f(c.get('elo_home')) is not None and f(c.get('elo_away')) is not None else None),
        ('GOALIE', 'the two men in net, nothing else',
         lambda c, m: (f(c.get('home_goalie_gsaa')) - f(c.get('away_goalie_gsaa')))
         if f(c.get('home_goalie_gsaa')) is not None and f(c.get('away_goalie_gsaa')) is not None else None),
        ('XG', 'chance quality - who should score, not who did',
         lambda c, m: ((f(c.get('home_xgf_per60')) - f(c.get('home_xga_per60')))
                       - (f(c.get('away_xgf_per60')) - f(c.get('away_xga_per60'))))
         if all(f(c.get(k)) is not None for k in
                ('home_xgf_per60', 'home_xga_per60', 'away_xgf_per60', 'away_xga_per60')) else None),
        ('REST', 'schedule and fatigue alone',
         lambda c, m: (f(c.get('home_rest_days')) - f(c.get('away_rest_days')))
         if f(c.get('home_rest_days')) is not None and f(c.get('away_rest_days')) is not None else None),
    ],
    'NBA': [
        ('ELO', 'pure strength rating',
         lambda c, m: (f(c.get('elo_home')) - f(c.get('elo_away')))
         if f(c.get('elo_home')) is not None and f(c.get('elo_away')) is not None else None),
        ('NETRTG', 'points per 100 possessions, both ends',
         lambda c, m: (f(c.get('home_net_rating')) - f(c.get('away_net_rating')))
         if f(c.get('home_net_rating')) is not None and f(c.get('away_net_rating')) is not None else None),
        ('OFFRTG', 'offence only - who can score on anybody',
         lambda c, m: (f(c.get('home_off_rating')) - f(c.get('away_off_rating')))
         if f(c.get('home_off_rating')) is not None and f(c.get('away_off_rating')) is not None else None),
        ('REST', 'rest and back-to-backs alone',
         lambda c, m: (f(c.get('home_rest_days')) - f(c.get('away_rest_days')))
         if f(c.get('home_rest_days')) is not None and f(c.get('away_rest_days')) is not None else None),
    ],
    'NFL': [
        ('EFF', 'EPA-based offensive rating differential',
         lambda c, m: (f(c.get('home_off_rating')) - f(c.get('away_off_rating')))
         if f(c.get('home_off_rating')) is not None and f(c.get('away_off_rating')) is not None else None),
        ('REST', 'rest edge and short weeks alone',
         lambda c, m: (f(c.get('home_rest')) - f(c.get('away_rest')))
         if f(c.get('home_rest')) is not None and f(c.get('away_rest')) is not None else None),
    ],
    'NCAAF': [
        ('SPPLUS', 'the composite power rating',
         lambda c, m: (f(c.get('home_sp_plus')) - f(c.get('away_sp_plus')))
         if f(c.get('home_sp_plus')) is not None and f(c.get('away_sp_plus')) is not None else None),
    ],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(CTX), action='append')
    ap.add_argument('--since', default='2026-09-01')
    a = ap.parse_args()

    for sport in (a.sport or ['NHL', 'NBA', 'NFL', 'NCAAF']):
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
        rows = []
        for c in ctxs:
            g = res.get((nz(c.get('away_team')), nz(c.get('home_team')),
                         str(c.get('game_date'))[:10]))
            if not g:
                continue
            cs = f(c.get('close_spread'))
            rows.append({
                'ctx': c,
                'mc': jd(c.get('mc_probabilities')),
                'actual': f(g['home_score']) - f(g['away_score']),
                'market': (None if cs is None else -(HCAP_SIGN[sport] * cs)),
            })
        bar = '=' * 100
        print(f'\n{bar}\n{sport}  ·  {len(rows)} games with a final score since '
              f'{a.since}\n{bar}')
        if len(rows) < 25:
            print('  too few games to fit - skipping')
            continue
        mk = [r for r in rows if r['market'] is not None]
        if mk:
            mkt_mae = sum(abs(r['actual'] - r['market']) for r in mk) / len(mk)
            print(f'  market MAE (the bar every voice is judged against): '
                  f'{mkt_mae:.2f} pts on n={len(mk)}')

        fitted = {}
        print(f'\n  {"voice":<9}{"n":>5}{"slope":>9}{"HFA":>8}{"corr":>7}'
              f'{"MAE":>8}  angle')
        for name, angle, fn in VOICES.get(sport, []):
            pts = []
            for r in rows:
                raw = fn(r['ctx'], r['mc'])
                if raw is None:
                    continue
                pts.append((raw, r['actual']))
            if len(pts) < 25:
                print(f'  {name:<9}{len(pts):>5}   n<25 — cannot fit')
                continue
            out = fit([p[0] for p in pts], [p[1] for p in pts])
            if not out:
                print(f'  {name:<9}{len(pts):>5}   no variance in this gap')
                continue
            b, a0, r, mae, n = out
            fitted[name] = (b, a0, fn)
            print(f'  {name:<9}{n:>5}{b:>9.4f}{a0:>+8.2f}{r:>7.2f}{mae:>8.2f}'
                  f'  {angle}')
        print('\n  slope converts that voice\'s own units into points · HFA is the')
        print('  intercept, i.e. how much this voice credits the home side with')
        print('  all gaps equal — the voices are ALLOWED to disagree about it.')

        # ── the test that matters: are they actually different opinions? ──
        names = sorted(fitted)
        if len(names) >= 2:
            preds = {}
            for nm in names:
                b, a0, fn = fitted[nm]
                preds[nm] = {}
                for i, r in enumerate(rows):
                    raw = fn(r['ctx'], r['mc'])
                    if raw is not None:
                        preds[nm][i] = a0 + b * raw
            print(f'\n  PAIRWISE CORRELATION OF THE FITTED OPINIONS')
            print(f'  {"":<9}' + ''.join(f'{x[:7]:>9}' for x in names))
            for i_nm in names:
                line = f'  {i_nm:<9}'
                for j_nm in names:
                    common = [k for k in preds[i_nm] if k in preds[j_nm]]
                    if len(common) < 15:
                        line += f'{"-":>9}'
                        continue
                    xs = [preds[i_nm][k] for k in common]
                    ys = [preds[j_nm][k] for k in common]
                    k2 = len(xs)
                    mx, my = sum(xs) / k2, sum(ys) / k2
                    sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / k2) or 1e-9
                    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / k2) or 1e-9
                    c2 = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (k2 * sx * sy)
                    line += f'{c2:>9.2f}'
                print(line)
            print('  Anything at 0.95+ off the diagonal is not a second opinion.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
