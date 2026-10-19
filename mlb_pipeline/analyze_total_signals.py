#!/usr/bin/env python3
"""Same rigour as the spread dive, aimed at TOTALS. Writes nothing.

== 2026-10-19 - WHY TOTALS ==
The spread dive closed four ways: no context feature, no movement feature, a
negative out-of-sample R2, and zero closing line value. But one real edge is
already on the books in this repo and it is a TOTAL, not a side -
project_wind_under_lens_1009: wind -> UNDER at 55.6% on n=1,444. That is more
sample than the entire spread study, so totals are where a measured edge
already exists and nobody has mined around it.

TARGET. total_residual = actual_total - close_total. Positive means the game
went OVER the number. Predicting that is what beats a total.

WHY THIS IS A SEPARATE SCRIPT and not a flag on the spread one: the target,
the sign convention, the controls and the useful features all differ. A total
has no home/away, so the controls are always-OVER and always-UNDER, and the
weather and pace columns matter in a way they never did for sides.

Everything else is deliberately identical, because the discipline is what
makes a positive result believable:
  * temporal split, early TRAIN / late TEST
  * Bonferroni over every feature actually tested, printed
  * sign must HOLD out of sample to count
  * controls first, so a "signal" has something to beat
  * n beside every number, thin slices refused

USAGE
    python analyze_total_signals.py --sport NCAAF
    python analyze_total_signals.py --all
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
import sys

import requests

import market_line as ML
import analyze_spread_signals as A

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
SCRATCH = os.environ.get('SCRATCH_DIR') or os.environ.get('TMPDIR') or '.'
BREAKEVEN = 52.38

RESULTS = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results',
           'NHL': 'nhl_game_results', 'MLB': 'mlb_game_results'}
CONTEXT = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context',
           'NHL': 'nhl_game_context', 'MLB': 'mlb_game_context'}


def f(v):
    return A.f(v)


def load_totals(sport: str):
    probe = requests.get(f'{SB}/rest/v1/{RESULTS[sport]}', headers=H,
                         params={'select': 'season', 'limit': '1'}, timeout=60)
    filt = ({'season': 'eq.2026'} if probe.status_code in (200, 206)
            else {'game_date': 'gte.2026-08-01'})
    res = A.page(RESULTS[sport], dict(filt, select='*'))
    ctx = {str(x['game_id']): x
           for x in A.page(CONTEXT[sport], {'select': '*'})}
    bridge = {str(c.get('results_game_id')): c for c in ctx.values()
              if c.get('results_game_id')}
    rows = []
    for r in res:
        hs, as_ = f(r.get('home_score')), f(r.get('away_score'))
        ct = f(r.get('close_total'))
        if hs is None or as_ is None or ct is None:
            continue
        c = ctx.get(str(r['game_id'])) or bridge.get(str(r['game_id']))
        if c is None:
            for cc in ctx.values():
                if (str(cc.get('home_team')) == str(r.get('home_team'))
                        and str(cc.get('away_team')) == str(r.get('away_team'))
                        and str(cc.get('game_date'))[:10]
                        == str(r.get('game_date'))[:10]):
                    c = cc
                    break
        if c is None:
            continue
        actual = hs + as_
        rows.append({'_gid': str(r['game_id']), '_actual': actual,
                     '_close': ct, '_resid': actual - ct,
                     '_week': f(r.get('week')),
                     '_date': str(r.get('game_date'))[:10],
                     '_total_result': r.get('total_result'), '_ctx': c})
    return rows


def attach_total_lines(rows, sport):
    p = os.path.join(SCRATCH, f'linefeat_{sport}_total.json')
    if not os.path.exists(p):
        print(f'  (no cached total-line features at {p})')
        return []
    with open(p, encoding='utf-8') as fh:
        lf = json.load(fh)
    keys, hit = set(), 0
    for r in rows:
        d = lf.get(r['_gid'])
        r['_has_lh'] = bool(d)
        if not d:
            continue
        hit += 1
        for k, v in d.items():
            r['_ctx'][f'lh_{k}'] = v
            keys.add(f'lh_{k}')
    print(f'  total-line movement joined on {hit}/{len(rows)} games')
    return sorted(keys) if hit >= 25 else []


def run(sport: str) -> None:
    print(f'\n{"="*74}\n{sport} TOTAL-RESIDUAL SIGNAL SEARCH\n{"="*74}')
    rows = load_totals(sport)
    print(f'graded games with a close_total + context: {len(rows)}')
    if len(rows) < 60:
        print('  too few to analyse - refusing')
        return
    # sanity: the table's own total_result must agree with our residual
    ok = tot = 0
    for r in rows:
        tr = str(r.get('_total_result') or '')
        if tr not in ('over', 'under'):
            continue
        tot += 1
        ok += (('over' if r['_resid'] > 0 else 'under') == tr)
    if tot:
        print(f'residual agrees with stored total_result on '
              f'{100*ok/tot:.1f}% of {tot} graded')
        if ok / tot < 0.90:
            print('  CONVENTION MISMATCH - refusing to analyse')
            return
    print(f'market MAE vs actual total: '
          f'{st.fmean(abs(r["_resid"]) for r in rows):.3f}   '
          f'residual sd {st.pstdev([r["_resid"] for r in rows]):.3f}')

    feats = A.numeric_features(rows)
    feats += A.add_differentials(rows, feats)
    feats += attach_total_lines(rows, sport)
    feats = sorted(set(feats))
    print(f'candidate features: {len(feats)}')

    wk = [r['_week'] for r in rows if r['_week'] is not None]
    if wk and len(set(wk)) > 2:
        cut = st.median(wk)
        train = [r for r in rows if (r['_week'] or 0) <= cut]
        test = [r for r in rows if (r['_week'] or 0) > cut]
        how = f'week <= {cut:g} / > {cut:g}'
    else:
        ds = sorted({r['_date'] for r in rows})
        cut = ds[int(len(ds) * 0.6)]
        train = [r for r in rows if r['_date'] <= cut]
        test = [r for r in rows if r['_date'] > cut]
        how = f'date <= {cut}'
    print(f'split: {how}  ->  TRAIN {len(train)}  TEST {len(test)}')
    if len(train) < 30 or len(test) < 20:
        print('  split too thin - refusing')
        return

    print('\n-- CONTROLS on TEST --')
    nz = [r for r in test if r['_resid'] != 0]
    ov = sum(1 for r in nz if r['_resid'] > 0)
    lo, hi = A.wilson(ov, len(nz))
    print(f'  always OVER   {ov}-{len(nz)-ov}  {100*ov/max(len(nz),1):.1f}%  '
          f'CI[{lo:.1f},{hi:.1f}]')
    lo, hi = A.wilson(len(nz) - ov, len(nz))
    print(f'  always UNDER  {len(nz)-ov}-{ov}  '
          f'{100*(len(nz)-ov)/max(len(nz),1):.1f}%  CI[{lo:.1f},{hi:.1f}]')

    tested = []
    for k in feats:
        xs, ys = [], []
        for r in train:
            v = f(r['_ctx'].get(k))
            if v is None:
                continue
            xs.append(v)
            ys.append(r['_resid'])
        if len(xs) < max(30, int(0.5 * len(train))):
            continue
        c = A.corr_ci(xs, ys)
        if c:
            tested.append((k, c))
    if not tested:
        print('  no feature had enough coverage')
        return
    bonf = 0.05 / len(tested)
    tested.sort(key=lambda t: t[1][3])
    print(f'\n-- UNIVARIATE on TRAIN: {len(tested)} tested, '
          f'Bonferroni p < {bonf:.5f} --')
    print(f'  {"feature":<34}{"r":>8}{"95% CI":>20}{"p":>10}{"n":>6}')
    for k, (r, lo_, hi_, p, nn) in tested[:14]:
        mark = ('  ** CLEARS BONFERRONI' if p < bonf
                else ('  (nominal)' if p < 0.05 else ''))
        print(f'  {k:<34}{r:>+8.3f}  [{lo_:+.3f},{hi_:+.3f}]'
              f'{p:>10.4f}{nn:>6}{mark}')
    surv = [(k, c) for k, c in tested if c[3] < bonf]
    nom = [(k, c) for k, c in tested if c[3] < 0.05]
    print(f'\n  clears Bonferroni: {len(surv)}   nominal p<.05: {len(nom)} '
          f'(chance expectation {0.05*len(tested):.1f})')

    check = surv or nom[:8]
    if not check:
        print('  nothing to validate')
        return
    print(f'\n-- TEST VALIDATION --')
    print(f'  {"feature":<34}{"train r":>9}{"test r":>9}{"sign":>8}'
          f'{"extreme-tercile O/U":>22}')
    held = []
    for k, (rtr, *_rest) in check:
        xs, ys = [], []
        for r in test:
            v = f(r['_ctx'].get(k))
            if v is None:
                continue
            xs.append(v)
            ys.append(r['_resid'])
        if len(xs) < 20:
            print(f'  {k:<34}{rtr:>+9.3f}     thin TEST coverage')
            continue
        c2 = A.corr_ci(xs, ys)
        rte = c2[0] if c2 else 0.0
        same = (rtr > 0) == (rte > 0)
        # ══ 2026-10-19 · SORT BY x ONLY ══
        # sorted(zip(xs, ys)) orders by x THEN y, so inside a tie group at one
        # x value the prefix/suffix picks the most extreme OUTCOMES - that is
        # selection on the dependent variable, and it manufactured a 79.0%
        # "BEATS" on h2h_last5_home_wins, a feature whose correlation had
        # FLIPPED out of sample. Features with big tie groups (h2h counts,
        # ATS-record integers) are exactly where it bites. Python's sort is
        # stable, so keying on x alone leaves ties in their original order and
        # the outcome cannot influence membership.
        pairs = sorted(zip(xs, ys), key=lambda t: t[0])
        t3 = (pairs[-max(6, len(pairs)//3):] if rtr > 0
              else pairs[:max(6, len(pairs)//3)])
        # betting the side the feature points at: OVER if r>0 else UNDER
        w = sum(1 for _x, y in t3 if (y > 0) == (rtr > 0))
        nn = sum(1 for _x, y in t3 if y != 0)
        lo2, hi2 = A.wilson(w, nn)
        verdict = ('BEATS' if lo2 > BREAKEVEN
                   else ('LOSES' if hi2 < BREAKEVEN else 'incon'))
        print(f'  {k:<34}{rtr:>+9.3f}{rte:>+9.3f}'
              f'{("HELD" if same else "FLIP"):>8}'
              f'   {w}-{nn-w} {100*w/max(nn,1):>5.1f}% [{lo2:.0f},{hi2:.0f}] {verdict}')
        if same:
            held.append(k)
    print(f'\n  held sign out-of-sample: {len(held)}/{len(check)} -> '
          f'{held if held else "NONE"}')

    # the documented lens, tested directly on this slice
    for wk_key in ('wind', 'temp'):
        v = [r for r in rows
             if f(r['_ctx'].get(wk_key)) is not None and r['_resid'] != 0]
        if len(v) < 40:
            continue
        thr = st.median(f(r['_ctx'][wk_key]) for r in v)
        hi_g = [r for r in v if f(r['_ctx'][wk_key]) > thr]
        if len(hi_g) < 20:
            continue
        un = sum(1 for r in hi_g if r['_resid'] < 0)
        lo3, hi3 = A.wilson(un, len(hi_g))
        print(f'\n  {wk_key} > {thr:g} (median): UNDER hit {un}-{len(hi_g)-un}  '
              f'{100*un/len(hi_g):.1f}%  CI[{lo3:.1f},{hi3:.1f}]  n={len(hi_g)}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args()
    for sp in (['NCAAF', 'NFL', 'NHL'] if a.all else [a.sport]):
        if sp in RESULTS:
            run(sp)
    return 0


if __name__ == '__main__':
    sys.exit(main())
