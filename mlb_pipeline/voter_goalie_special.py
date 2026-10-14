#!/usr/bin/env python3
"""The goalie-and-special-teams panelist — NHL's second voice.

== 2026-10-14 - WHY THIS EXISTS ==
ANDY: "we cant just accept nhl having one voice."

He is right, and NHL was the worst case in the panel audit: `projected`,
`goals` and `mc` agree on side 100.0% of the time on all three pairs, because
nhl_game_context.py:158-160 writes all three out of ONE Elo predictor. Our most
nightly-active sport had one analyst wearing three nameplates.

The first attempt at a second voice failed, and failed instructively. Rolling
form (voter_rolling_form.py) predicted 6.11 goals with sd 0.057 against a market
sd of 0.382 -- it WAS a constant, and its apparently informative slope
(+0.567, CI[+0.202,+0.900]) was matched by a literal constant (+0.564,
CI[+0.212,+0.885]). NHL team scoring rates barely differ, so team form cannot
differentiate NHL games.

What DOES differentiate an NHL game is the GOALIE, and Elo reads none of it.
Elo is a win/loss record; it does not know who is in net, whether a team is on
the second of a back-to-back, or how the special teams match up. That is a
genuinely separate information set, which is the only thing that makes a
panelist independent.

== SIGNS AND MAGNITUDES, VERIFIED AGAINST ACTUAL GOALS BEFORE ANY EQUATION ==
n=142-156 graded games:

  away_goalie_gsaa   -> total   corr -0.143   0.34 goals per 1sd   [correct]
  away_goalie_sv_pct -> total   corr -0.141   0.33 goals per 1sd   [correct]
  home_pp_pct        -> total   corr +0.152   0.35 goals per 1sd   [correct]
  home_xgf_per60     -> margin  corr +0.177   0.49 goals per 1sd   [correct]
  away_xgf_per60     -> margin  corr -0.144   0.40 goals per 1sd   [correct]
  home_back_to_back  -> margin  slope -0.876 goals                 [correct]

GSAA is Goals Saved Above Average, so HIGHER is a BETTER goalie and FEWER goals
-- the negative slope on total confirms it. These effects are 3-6x the 0.057
differentiation rolling form managed, which is the whole point.

== WHY ONLY THREE FEATURES ==
There are 156 graded games with context, 137 carrying all ten candidate inputs.
Ten features on 137 rows is 13 rows per feature and would overfit outright. So
each market gets THREE features, picked on correct sign plus largest measured
effect, fixed before fitting. Several other candidates had the WRONG sign at
this sample size (home_goalie_sv_pct +2.42 on total, home_high_danger_for
-0.121, home_pk_pct +0.116) and were excluded for that reason rather than
because they scored badly -- a wrong sign at n=142 is noise, and fishing for the
ones that happen to fit is how the 65-79% ATS backtests happened.

== WHAT IS AND IS NOT CLAIMED ==
TOTAL is the only gradeable market. NHL's close_puckline is a fixed +/-1.5
handicap (2-3 distinct values), not a margin forecast, so a margin panelist
cannot be scored against it; margin is written for completeness and marked
unseatable.

At n=137 nothing here will be PROVEN. The goal is a DISTINCT voice with a real
spread of predictions, which NHL does not currently have at all. It earns a
seat only if audit_constant_control.py says REAL and
audit_panelist_independence.py says it is not another nameplate for Elo.

DATA GAP FOUND, worth fixing separately: home/away_goalie_last5_sv_pct,
l10_goals_per_game and l10_goals_against_per_game are ALL EMPTY (0 of 202),
which is very likely why nothing in the NHL stack reads recent form.

USAGE
    python voter_goalie_special.py --backfill
    python voter_goalie_special.py --today
    python voter_goalie_special.py --backfill --dry-run
"""
from __future__ import annotations

import argparse
import json
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
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

PANELIST = 'goalie_special'
MODEL_VERSION = 'goalie_special_v1.0.0'
#: 137 rows with all inputs; 3 features is ~45 rows per feature.
MIN_TRAIN = 90
SPORT = 'NHL'
CONTEXT, RESULTS = 'nhl_game_context', 'nhl_game_results'


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _g(c, k):
    return _f(c.get(k))


def _b(c, k):
    v = c.get(k)
    if v is None:
        return None
    return 1.0 if v in (True, 1, '1', 't', 'true', 'True') else 0.0


#: Three features per market, fixed before fitting. Sign-verified above.
def _total_feats(c):
    gh, ga = _g(c, 'home_goalie_gsaa'), _g(c, 'away_goalie_gsaa')
    pph, ppa = _g(c, 'home_pp_pct'), _g(c, 'away_pp_pct')
    xh, xa = _g(c, 'home_xgf_per60'), _g(c, 'away_xgf_per60')
    if None in (gh, ga, pph, ppa, xh, xa):
        return None
    return [gh + ga,          # combined goalie quality: higher -> fewer goals
            pph + ppa,        # combined powerplay: higher -> more goals
            xh + xa]          # combined expected offence


def _margin_feats(c):
    xh, xa = _g(c, 'home_xgf_per60'), _g(c, 'away_xgf_per60')
    gh, ga = _g(c, 'home_goalie_gsaa'), _g(c, 'away_goalie_gsaa')
    bh, ba = _b(c, 'home_back_to_back'), _b(c, 'away_back_to_back')
    if None in (xh, xa, gh, ga, bh, ba):
        return None
    return [xh - xa,          # expected-offence edge
            gh - ga,          # goalie edge
            ba - bh]          # rest edge: away tired helps home


FEATS = {'total': _total_feats, 'margin': _margin_feats}
NAMES = {'total': ('goalie_sum', 'pp_sum', 'xgf_sum'),
         'margin': ('xgf_edge', 'goalie_edge', 'rest_edge')}
#: Only `total` can be graded against the market — see the docstring.
SEATABLE_MARKETS = ('total',)


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


def load(market):
    ctx = page(CONTEXT, {'select': '*'})
    res = page(RESULTS, {'select': '*'})
    by_id, by_key = {}, {}
    for r in res:
        if r.get('game_id'):
            by_id[str(r['game_id'])] = r
        by_key[(str(r.get('game_date'))[:10], str(r.get('home_team')),
                str(r.get('away_team')))] = r
    fn = FEATS[market]
    out = []
    for c in ctx:
        v = fn(c)
        if v is None:                 # no fallback: missing input, no pick
            continue
        g = (by_id.get(str(c.get('game_id')))
             or by_key.get((str(c.get('game_date'))[:10],
                            str(c.get('home_team')), str(c.get('away_team')))))
        hs = _f((g or {}).get('home_score'))
        as_ = _f((g or {}).get('away_score'))
        y = None
        if hs is not None and as_ is not None:
            y = (hs + as_) if market == 'total' else (hs - as_)
        out.append({'game_id': str(c.get('game_id')),
                    'date': str(c.get('game_date'))[:10],
                    'x': v, 'y': y})
    out.sort(key=lambda r: r['date'])
    return out


def fit(rows):
    tr = [r for r in rows if r['y'] is not None]
    if len(tr) < MIN_TRAIN:
        return None
    X = np.column_stack([np.ones(len(tr))]
                        + [np.array([r['x'][i] for r in tr], dtype=float)
                           for i in range(len(tr[0]['x']))])
    y = np.array([r['y'] for r in tr], dtype=float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def predict(beta, r):
    return float(np.concatenate(([1.0], np.array(r['x'], dtype=float))) @ beta)


def upsert(payload, dry):
    if dry or not payload:
        return 0
    done = 0
    for i in range(0, len(payload), 500):
        chunk = payload[i:i + 500]
        r = requests.post(f'{SB}/rest/v1/panelist_predictions', headers=H_WRITE,
                          params={'on_conflict':
                                  'sport,game_id,panelist,market'},
                          data=json.dumps(chunk), timeout=180)
        if r.status_code not in (200, 201, 204):
            print(f'    WRITE FAILED {r.status_code}: {r.text[:200]}')
            continue
        done += len(chunk)
    return done


def run(market, mode, dry):
    rows = load(market)
    graded = [r for r in rows if r['y'] is not None]
    seat = ' (gradeable)' if market in SEATABLE_MARKETS else \
           ' (NOT gradeable — puck line is a fixed handicap)'
    print(f'\n{SPORT} - {market}{seat}: {len(rows)} games with all inputs, '
          f'{len(graded)} graded')
    if len(graded) < MIN_TRAIN:
        print(f'  below MIN_TRAIN={MIN_TRAIN} — refusing to fit')
        return
    payload = []
    if mode == 'backfill':
        by_date = defaultdict(list)
        for r in rows:
            by_date[r['date']].append(r)
        fits = skipped = 0
        for d in sorted(by_date):
            beta = fit([r for r in graded if r['date'] < d])
            if beta is None:
                skipped += len(by_date[d])
                continue
            fits += 1
            for r in by_date[d]:
                payload.append({
                    'sport': SPORT, 'game_id': r['game_id'],
                    'game_date': r['date'], 'panelist': PANELIST,
                    'market': market,
                    'prediction': round(predict(beta, r), 3),
                    'pregame': False,
                    'inputs': dict(zip(NAMES[market], r['x'])),
                    'model_version': MODEL_VERSION})
        print(f'  walk-forward: {fits} refits, {len(payload)} predictions, '
              f'{skipped} skipped for thin history')
        b = fit(graded)
        if b is not None:
            print('  final coefficients (all graded, for reading only):')
            for n, v in zip(('intercept',) + NAMES[market], b):
                print(f'    {n:<13}{v:+10.4f}')
    else:
        beta = fit(graded)
        if beta is None:
            print('  cannot fit')
            return
        for r in [x for x in rows if x['y'] is None]:
            payload.append({
                'sport': SPORT, 'game_id': r['game_id'],
                'game_date': r['date'], 'panelist': PANELIST,
                'market': market, 'prediction': round(predict(beta, r), 3),
                'pregame': True,
                'inputs': dict(zip(NAMES[market], r['x'])),
                'model_version': MODEL_VERSION})
        print(f'  live: fitted on {len(graded)}, {len(payload)} unplayed')
    if payload:
        v = [p['prediction'] for p in payload]
        print(f'  prediction mean {np.mean(v):+.2f} sd {np.std(v):.3f} '
              f'range [{min(v):+.2f}, {max(v):+.2f}]')
        print(f'  (rolling form managed sd 0.057 here against market sd 0.382 '
              f'— that is the bar to clear)')
    wrote = upsert(payload, dry)
    print(f'  {"DRY RUN — would write" if dry else "wrote"} '
          f'{len(payload) if dry else wrote} rows')


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--backfill', action='store_true')
    g.add_argument('--today', action='store_true')
    ap.add_argument('--market', choices=('margin', 'total'))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    mode = 'backfill' if a.backfill else 'today'
    print(f'GOALIE + SPECIAL-TEAMS PANELIST {MODEL_VERSION} — mode={mode}')
    print('NHL had ONE voice: projected/goals/mc agree 100% because all three '
          'come from one Elo predictor.')
    for m in ([a.market] if a.market else ['total', 'margin']):
        try:
            run(m, mode, a.dry_run)
        except Exception as e:                                 # noqa: BLE001
            print(f'\n{m}: FAILED {type(e).__name__}: {str(e)[:140]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
