#!/usr/bin/env python3
"""Deep dive: does ANY signal we hold predict the spread residual? Writes nothing.

== 2026-10-18 - WHY ==
ANDY: "dont fucking tellme there is no measured edge, you are not digging deep
enough ... we need to evalute relsitonship of rolling stats, moeny flow, all
data we have. ... game picks need to be better bottom line."

The earlier pass tested six stored PROJECTION columns against the close and
stopped. That was not a search for signal, it was a check on one output. This
searches the inputs: every numeric context column, matchup DIFFERENTIALS built
from the paired ones, and the market-movement features from line_history
(1.69M NCAAF / 721k NFL / 356k NHL per-book captures, 100% pregame).

TARGET. residual = actual_home_margin - market_expected_home_margin. That is
the only thing worth predicting: beating the spread means predicting where the
close is WRONG, not predicting the game.

THE SIGN IS DERIVED, NEVER ASSUMED. close_spread means opposite things by
sport in this schema (project_close_spread_sign_bug_914): NCAAF stores a HOME
HANDICAP, NFL stores the HOME EXPECTED MARGIN. Both candidate signs are scored
against the table's own spread_result and the winner must agree on >90% of
graded games, or the sport is skipped rather than analysed under a guess.

DISCIPLINE, each from a trap already paid for:
  * TEMPORAL SPLIT. Early weeks TRAIN, later weeks TEST. A feature is only
    reported if it clears Bonferroni on TRAIN *and* keeps its sign on TEST.
    TRAIN is a ceiling, TEST is the result.
  * BONFERRONI over every feature actually tested, printed explicitly.
  * CONSTANT + always-HOME + always-DOG controls, because a slope that a
    constant matches is the market level negated, not a signal.
  * NO VACUOUS PASSES. n is printed beside every number and thin slices are
    refused rather than quoted.

USAGE
    python analyze_spread_signals.py --sport NCAAF
    python analyze_spread_signals.py --sport NFL --min-n 60
    python analyze_spread_signals.py --all
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
SCRATCH = os.environ.get('SCRATCH_DIR') or os.environ.get('TMPDIR') or '.'

SPORTS = {
    'NCAAF': {'results': 'ncaaf_game_results', 'context': 'ncaaf_game_context',
              'line': 'close_spread', 'train_weeks': (0, 4)},
    'NFL':   {'results': 'nfl_game_results', 'context': 'nfl_game_context',
              'line': 'close_spread', 'train_weeks': (1, 4)},
    'NHL':   {'results': 'nhl_game_results', 'context': 'nhl_game_context',
              'line': 'close_puckline', 'train_weeks': None},
}


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def page(t, p):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, timeout=240,
                         params=dict(p, limit='1000', offset=str(off)))
        if r.status_code not in (200, 206):
            print(f'  !! {t} HTTP {r.status_code} {r.text[:170]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def wilson(w, n):
    if not n:
        return (0.0, 0.0)
    z = 1.96
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def corr_ci(xs, ys):
    """Pearson r with a Fisher-z 95% CI and a two-sided p."""
    n = len(xs)
    if n < 10 or len(set(xs)) < 3:
        return None
    try:
        r = st.correlation(xs, ys)
    except Exception:                                          # noqa: BLE001
        return None
    if abs(r) >= 0.999:
        return None
    z = 0.5 * math.log((1 + r) / (1 - r))
    se = 1 / math.sqrt(n - 3)
    lo = math.tanh(z - 1.96 * se)
    hi = math.tanh(z + 1.96 * se)
    t = abs(z) / se
    p = math.erfc(t / math.sqrt(2))
    return r, lo, hi, p, n


def derive_sign(rows, line_key):
    """+1 if close_spread is the HOME EXPECTED MARGIN, -1 if a HOME HANDICAP.

    Scored against the table's own spread_result; the winner must agree on
    more than 90% or the caller skips the sport.
    """
    best = (0.0, None)
    for sgn in (1, -1):
        ok = tot = 0
        for r in rows:
            sr = str(r.get('spread_result') or '')
            if sr not in ('home_covered', 'away_covered'):
                continue
            mk = sgn * r['_line']
            pred = 'home_covered' if r['_margin'] > mk else 'away_covered'
            ok += (pred == sr)
            tot += 1
        if tot:
            score = ok / tot
            if score > best[0]:
                best = (score, sgn)
    return best[1], best[0]


def load(sport: str):
    cfg = SPORTS[sport]
    # SCHEMA-TOLERANT: nhl_game_results has no `season` column, and a 400 on
    # an explicit filter reads back as an empty table (the "explicit SELECT =
    # silent blanks" trap). Probe, then fall back to a date window.
    probe = requests.get(f'{SB}/rest/v1/{cfg["results"]}', headers=H,
                         params={'select': 'season', 'limit': '1'}, timeout=60)
    if probe.status_code in (200, 206):
        res = page(cfg['results'], {'select': '*', 'season': 'eq.2026'})
    else:
        res = page(cfg['results'],
                   {'select': '*', 'game_date': 'gte.2026-08-01'})
    ctx = {str(x['game_id']): x
           for x in page(cfg['context'], {'select': '*'})}
    # context may key on its own id and carry a bridge to results
    bridge = {}
    for gid, c in ctx.items():
        rg = c.get('results_game_id')
        if rg:
            bridge[str(rg)] = c
    rows = []
    for r in res:
        hs, as_ = f(r.get('home_score')), f(r.get('away_score'))
        ln = f(r.get(cfg['line'])) if cfg['line'] in r else None
        if ln is None:
            ln = f(r.get('close_spread'))
        if hs is None or as_ is None or ln is None:
            continue
        c = (ctx.get(str(r['game_id'])) or bridge.get(str(r['game_id'])))
        if c is None:
            # fall back to (teams, date), the only join that survives the
            # two id schemes in nfl_game_results
            for cc in ctx.values():
                if (str(cc.get('home_team')) == str(r.get('home_team'))
                        and str(cc.get('away_team')) == str(r.get('away_team'))
                        and str(cc.get('game_date'))[:10]
                        == str(r.get('game_date'))[:10]):
                    c = cc
                    break
        if c is None:
            continue
        rows.append({'_gid': str(r['game_id']), '_margin': hs - as_,
                     '_line': ln, '_week': f(r.get('week')),
                     '_date': str(r.get('game_date'))[:10],
                     'spread_result': r.get('spread_result'),
                     '_ctx': c})
    return rows


def numeric_features(rows, min_cover=0.60):
    """Columns numeric on at least min_cover of rows. Row 0 is not a schema."""
    counts = defaultdict(int)
    for r in rows:
        for k, v in r['_ctx'].items():
            if isinstance(v, bool):
                continue
            if f(v) is not None:
                counts[k] += 1
    n = len(rows)
    skip = {'season', 'week', 'season_week', 'close_spread', 'open_spread',
            'close_total', 'open_total', 'close_home_ml', 'close_away_ml',
            'open_home_ml', 'open_away_ml', 'home_ml_close', 'home_ml_open',
            'home_ml_odds', 'away_ml_odds', 'current_total'}
    return sorted(k for k, c in counts.items()
                  if c >= min_cover * n and k not in skip)


def add_differentials(rows, feats):
    """home_X minus away_X, and offence-vs-opposing-defence matchups.

    A raw level ("home_off_epa_pp") is mostly team quality, which the close
    already prices. The DIFFERENTIAL is the part a market can misprice.
    """
    pairs = []
    for k in feats:
        if k.startswith('home_'):
            a = 'away_' + k[5:]
            if a in feats:
                pairs.append((k[5:], k, a))
    made = []
    for name, hk, ak in pairs:
        key = f'diff_{name}'
        for r in rows:
            hv, av = f(r['_ctx'].get(hk)), f(r['_ctx'].get(ak))
            r['_ctx'][key] = (hv - av) if (hv is not None
                                           and av is not None) else None
        made.append(key)
    # explicit offence-vs-defence matchups where both sides exist
    for off, dfn, label in (
            ('home_off_epa_pp', 'away_def_epa_pp', 'mm_home_off_vs_away_def'),
            ('away_off_epa_pp', 'home_def_epa_pp', 'mm_away_off_vs_home_def'),
            ('home_pass_epa_pg', 'away_def_pass_epa_allowed', 'mm_home_pass'),
            ('away_pass_epa_pg', 'home_def_pass_epa_allowed', 'mm_away_pass'),
            ('home_rush_epa_pg', 'away_def_rush_epa_allowed', 'mm_home_rush'),
            ('away_rush_epa_pg', 'home_def_rush_epa_allowed', 'mm_away_rush')):
        hit = 0
        for r in rows:
            a, b = f(r['_ctx'].get(off)), f(r['_ctx'].get(dfn))
            r['_ctx'][label] = (a - b) if (a is not None
                                            and b is not None) else None
            hit += r['_ctx'][label] is not None
        if hit >= 0.6 * len(rows):
            made.append(label)
    return made


def attach_line_features(rows, sport):
    p = os.path.join(SCRATCH, f'linefeat_{sport}_spread.json')
    if not os.path.exists(p):
        print(f'  (no cached line features at {p} - run '
              f'extract_line_features.py)')
        return []
    with open(p, encoding='utf-8') as fh:
        lf = json.load(fh)
    keys, hit = set(), 0
    for r in rows:
        d = lf.get(r['_gid'])
        if not d:
            continue
        hit += 1
        for k, v in d.items():
            r['_ctx'][f'lh_{k}'] = v
            keys.add(f'lh_{k}')
    print(f'  line-movement features joined on {hit}/{len(rows)} games')
    # 2026-10-18: do NOT silently drop them for low coverage. line_history
    # covers a SUBSET of games, and the right treatment is to analyse that
    # subset explicitly (see the prop-projection lesson: always split by
    # whether the inputs were present) rather than to discard the only
    # money-flow data we own. The caller marks covered rows so the scan can
    # restrict to them.
    for r in rows:
        r['_has_lh'] = ('lh_close_line' in r['_ctx'])
    return sorted(keys) if hit >= 25 else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--min-n', type=int, default=40)
    a = ap.parse_args()
    todo = ['NCAAF', 'NFL', 'NHL'] if a.all else [a.sport]
    for sport in todo:
        if sport not in SPORTS:
            continue
        print(f'\n{"="*74}\n{sport} SPREAD-RESIDUAL SIGNAL SEARCH\n{"="*74}')
        rows = load(sport)
        print(f'graded games joined to context: {len(rows)}')
        if len(rows) < a.min_n:
            print('  too few to analyse - refusing rather than quoting noise')
            continue
        sgn, agree = derive_sign(rows, SPORTS[sport]['line'])
        print(f'sign of {SPORTS[sport]["line"]}: '
              f'{"HOME EXPECTED MARGIN (+)" if sgn==1 else "HOME HANDICAP (-)"}'
              f'  agrees with spread_result on {100*agree:.1f}%')
        if agree < 0.90:
            print('  CONVENTION NOT ESTABLISHED (<90%) - skipping this sport '
                  'rather than analysing under a guess')
            continue
        for r in rows:
            r['_mkt'] = sgn * r['_line']
            r['_resid'] = r['_margin'] - r['_mkt']
            r['_home_cov'] = r['_resid'] > 0
        pushes = sum(1 for r in rows if r['_resid'] == 0)
        print(f'market MAE vs actual margin: '
              f'{st.fmean(abs(r["_mkt"]-r["_margin"]) for r in rows):.3f}'
              f'   residual sd: {st.pstdev([r["_resid"] for r in rows]):.3f}'
              f'   pushes: {pushes}')

        feats = numeric_features(rows)
        feats += add_differentials(rows, feats)
        feats += attach_line_features(rows, sport)
        feats = sorted(set(feats))
        print(f'candidate features: {len(feats)}')

        # ---- temporal split ----
        wk = [r['_week'] for r in rows if r['_week'] is not None]
        if wk and len(set(wk)) > 2:
            cut = st.median(wk)
            train = [r for r in rows if (r['_week'] or 0) <= cut]
            test = [r for r in rows if (r['_week'] or 0) > cut]
            how = f'week <= {cut:g} TRAIN / > {cut:g} TEST'
        else:
            ds = sorted({r['_date'] for r in rows})
            cut = ds[int(len(ds) * 0.6)]
            train = [r for r in rows if r['_date'] <= cut]
            test = [r for r in rows if r['_date'] > cut]
            how = f'date <= {cut} TRAIN / > TEST'
        print(f'split: {how}  ->  TRAIN {len(train)}  TEST {len(test)}')
        if len(train) < 30 or len(test) < 20:
            print('  split too thin to validate - refusing')
            continue

        # ---- controls first, so any "signal" has something to beat ----
        print('\n-- CONTROLS on TEST --')
        hc = sum(1 for r in test if r['_home_cov'])
        n = sum(1 for r in test if r['_resid'] != 0)
        lo, hi = wilson(hc, n)
        print(f'  always HOME   {hc}-{n-hc}  {100*hc/max(n,1):.1f}%  '
              f'CI[{lo:.1f},{hi:.1f}]')
        lo, hi = wilson(n - hc, n)
        print(f'  always AWAY   {n-hc}-{hc}  {100*(n-hc)/max(n,1):.1f}%  '
              f'CI[{lo:.1f},{hi:.1f}]')
        dog = [r for r in test if r['_mkt'] != 0]
        dw = sum(1 for r in dog
                 if (r['_home_cov'] and r['_mkt'] < 0)
                 or ((not r['_home_cov']) and r['_mkt'] > 0))
        lo, hi = wilson(dw, len(dog))
        print(f'  always DOG    {dw}-{len(dog)-dw}  '
              f'{100*dw/max(len(dog),1):.1f}%  CI[{lo:.1f},{hi:.1f}]')

        # ---- univariate scan on TRAIN ----
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
            c = corr_ci(xs, ys)
            if c:
                tested.append((k, c))
        if not tested:
            print('\n  no feature had enough coverage to test')
            continue
        bonf = 0.05 / len(tested)
        print(f'\n-- UNIVARIATE on TRAIN: {len(tested)} features tested, '
              f'Bonferroni needs p < {bonf:.5f} --')
        tested.sort(key=lambda t: t[1][3])
        print(f'  {"feature":<34}{"r":>8}{"95% CI":>20}{"p":>10}{"n":>6}')
        for k, (r, lo_, hi_, p, nn) in tested[:12]:
            mark = '  ** CLEARS BONFERRONI' if p < bonf else (
                '  (nominal p<.05)' if p < 0.05 else '')
            print(f'  {k:<34}{r:>+8.3f}  [{lo_:+.3f},{hi_:+.3f}]'
                  f'{p:>10.4f}{nn:>6}{mark}')
        survivors = [(k, c) for k, c in tested if c[3] < bonf]
        nominal = [(k, c) for k, c in tested if c[3] < 0.05]
        print(f'\n  clears Bonferroni: {len(survivors)}   '
              f'nominal p<.05: {len(nominal)} '
              f'(expected by chance at this count: '
              f'{0.05*len(tested):.1f})')

        # ---- validate on TEST ----
        check = survivors or nominal[:8]
        if not check:
            print('  nothing to validate on TEST')
            continue
        print(f'\n-- TEST VALIDATION ({"Bonferroni survivors" if survivors else "nominal candidates, treated as exploratory"}) --')
        print(f'  {"feature":<34}{"train r":>9}{"test r":>9}{"sign":>7}'
              f'{"top-tercile ATS":>18}')
        held = []
        for k, (rtr, _lo, _hi, _p, _n) in check:
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
            c2 = corr_ci(xs, ys)
            rte = c2[0] if c2 else 0.0
            same = (rtr > 0) == (rte > 0)
            # ══ 2026-10-19 · SORT BY x ONLY ══
            # sorted(zip(xs, ys)) orders by x THEN y, so inside a tie group at
            # one x value the prefix/suffix picks the most extreme OUTCOMES -
            # selection on the dependent variable. It manufactured a 79.0%
            # "BEATS" on h2h_last5_home_wins, whose correlation had FLIPPED out
            # of sample. Features with big tie groups (h2h counts, ATS-record
            # integers) are exactly where it bites. Python's sort is stable, so
            # keying on x alone leaves ties in their original order and the
            # outcome cannot influence membership.
            pairs = sorted(zip(xs, ys), key=lambda t: t[0])
            t3 = pairs[-max(6, len(pairs)//3):] if rtr > 0 else pairs[:max(6, len(pairs)//3)]
            w = sum(1 for _x, y in t3 if y > 0)
            nn = sum(1 for _x, y in t3 if y != 0)
            lo2, hi2 = wilson(w, nn)
            print(f'  {k:<34}{rtr:>+9.3f}{rte:>+9.3f}{"HELD" if same else "FLIPPED":>7}'
                  f'   {w}-{nn-w} {100*w/max(nn,1):>5.1f}% CI[{lo2:.0f},{hi2:.0f}]')
            if same:
                held.append(k)
        print(f'\n  held sign out-of-sample: {len(held)}/{len(check)}'
              f'  -> {held if held else "NONE"}')
        if not held:
            print('  VERDICT: no feature in this sport survived a temporal '
                  'split. Any in-sample r was noise.')

        # ---- MONEY FLOW: the line-movement subset, analysed on its own ----
        lh = [r for r in rows if r.get('_has_lh')]
        lhkeys = sorted({k for r in lh for k in r['_ctx'] if k.startswith('lh_')})
        if len(lh) >= 40 and lhkeys:
            print()
            print(f'-- MARKET MOVEMENT subset: {len(lh)} games with '
                  f'line_history coverage, {len(lhkeys)} features --')
            lh.sort(key=lambda r: r['_date'])
            cut_i = int(len(lh) * 0.6)
            ltr, lte = lh[:cut_i], lh[cut_i:]
            print(f'   TRAIN {len(ltr)} (to {ltr[-1]["_date"]})  '
                  f'TEST {len(lte)} (from {lte[0]["_date"]})')
            res = []
            for k in lhkeys:
                xs = [f(r['_ctx'].get(k)) for r in ltr]
                ys = [r['_resid'] for r in ltr]
                pair = [(x, y) for x, y in zip(xs, ys) if x is not None]
                if len(pair) < 25:
                    continue
                c = corr_ci([p[0] for p in pair], [p[1] for p in pair])
                if c:
                    res.append((k, c))
            if not res:
                print('   no movement feature had enough coverage')
            else:
                b2 = 0.05 / len(res)
                res.sort(key=lambda t: t[1][3])
                print(f'   {len(res)} tested, Bonferroni p < {b2:.5f}')
                print(f'   {"feature":<26}{"train r":>9}{"p":>9}'
                      f'{"test r":>9}{"sign":>9}')
                for k, (rtr, _l, _h, p, _n) in res[:10]:
                    xs = [f(r['_ctx'].get(k)) for r in lte]
                    ys = [r['_resid'] for r in lte]
                    pr = [(x, y) for x, y in zip(xs, ys) if x is not None]
                    c2 = corr_ci([q[0] for q in pr], [q[1] for q in pr]) if len(pr) >= 20 else None
                    rte = c2[0] if c2 else float('nan')
                    same = (rtr > 0) == (rte > 0) if c2 else False
                    flag = '  ** BONF' if p < b2 else ''
                    print(f'   {k:<26}{rtr:>+9.3f}{p:>9.4f}{rte:>+9.3f}'
                          f'{("HELD" if same else "FLIPPED"):>9}{flag}')
            # the classic hypothesis, tested directly and priced-agnostic:
            # does the line MOVING toward a side predict that side covering?
            mv = [r for r in lh if f(r['_ctx'].get('lh_line_move')) is not None]
            for label, sel in (('line moved >= 1.0 toward HOME',
                                lambda r: f(r['_ctx']['lh_line_move']) >= 1.0),
                               ('line moved >= 1.0 toward AWAY',
                                lambda r: f(r['_ctx']['lh_line_move']) <= -1.0),
                               ('late_move >= 0.5 toward HOME',
                                lambda r: f(r['_ctx'].get('lh_late_move') or 0) >= 0.5),
                               ('late_move <= -0.5 toward AWAY',
                                lambda r: f(r['_ctx'].get('lh_late_move') or 0) <= -0.5)):
                v = [r for r in mv if sel(r) and r['_resid'] != 0]
                if len(v) < 20:
                    print(f'   {label:<34}n={len(v)} - too thin to quote')
                    continue
                w = sum(1 for r in v if r['_home_cov'])
                lo3, hi3 = wilson(w, len(v))
                print(f'   {label:<34}HOME covered {w}-{len(v)-w}  '
                      f'{100*w/len(v):.1f}%  CI[{lo3:.1f},{hi3:.1f}]  n={len(v)}')
        else:
            print(f'-- MARKET MOVEMENT subset too thin '
                  f'({len(lh)} games) --')
    return 0


if __name__ == '__main__':
    sys.exit(main())
