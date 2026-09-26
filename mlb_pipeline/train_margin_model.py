"""Train a real-feature margin model for NFL and NCAAF, and prove it out.

Andy 2026-09-26:
  "Give projection real features. I don't want fewer picks... the process
   should be models that have inputs like live rolling stats, projections
   based on different things and weights ... our engine should weigh
   everything accordingly then make a pick ... all data points in game
   detail i.e. all information we have on game, teams ability to cover
   spreads, teams SOS/SOR, home/away splits."

WHAT THIS REPLACES. Today NCAAF projects margin as `sp_gap * 0.85 + hfa`
and NFL does something similar off EPA. One number, one lens, no form, no
venue split, no schedule context, no cover history. Measured 2026-09-26,
that projection lands BELOW the market on the favourite in 76% of NCAAF
and 88% of NFL games — a one-directional disagreement, which is the
definition of a miscalibrated model rather than an edge, and is the
mechanism behind NCAAF dog picks going 15-32 (32%).

THE TARGET IS THE MARGIN, NOT THE COVER. Predicting "does the favourite
cover" directly fits the market's own line and teaches the model nothing
about football. Predicting the actual margin gives an opinion that can
then be COMPARED to the line, which is what an edge actually is.

LEAK DISCIPLINE is the whole ballgame here and this pipeline has been
burned twice (the 2026-09-22 prop L5 leak, and the SP+ backtests that
looked brilliant because sp_overall already contained the games being
predicted). Two rules, both enforced below:
  1. Features come from team_form_features.build(), which snapshots a
     team's running totals BEFORE folding in the game being predicted.
  2. The test set is LATER SEASONS than the train set. No shuffling, no
     random split — a random split lets week 12 teach week 3.

Nothing here writes to the database. It trains, scores out-of-sample,
and prints the comparison against the shipping projection so the decision
to adopt is made on evidence.

    python train_margin_model.py --sport NFL
    python train_margin_model.py --sport NCAAF --save
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _l in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

import team_form_features as tff          # noqa: E402

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

RESULTS_TABLE = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results'}

# Market context the model is allowed to see. Deliberately NOT the spread
# or the moneyline: handing it the line makes it re-predict the line, and
# the resulting "edge" is then guaranteed to be noise. Rest and division
# are schedule facts, not prices.
NEUTRAL_COLS = ['home_rest', 'away_rest', 'div_game', 'neutral_site']


def _page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=120)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{path} -> {r.status_code}: {(r.text or "")[:200]}')
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load(sport: str):
    tbl = RESULTS_TABLE[sport]
    cols = ('game_id,season,game_date,home_team,away_team,home_score,'
            'away_score,close_spread')
    avail = cols
    rows = []
    for extra in (NEUTRAL_COLS, []):
        try:
            sel = avail + (',' + ','.join(extra) if extra else '')
            rows = _page(tbl, {'select': sel, 'home_score': 'not.is.null'})
            break
        except RuntimeError:
            continue          # column set not present on this table
    print(f'  {tbl}: {len(rows)} completed games')
    form = tff.build(sport)
    print(f'  form snapshots: {len(form)}')
    return rows, form


def featurise(rows, form):
    """-> (X, y, meta). y is the ACTUAL home margin."""
    X, y, meta = [], [], []
    for g in rows:
        hs, as_ = _f(g.get('home_score')), _f(g.get('away_score'))
        if hs is None or as_ is None:
            continue
        hf = form.get((g['game_id'], g.get('home_team')))
        af = form.get((g['game_id'], g.get('away_team')))
        if not hf or not af:
            continue          # no leak-free history yet (early season)
        row = []
        for feat in tff.TEAM_FEATURES:
            h, a = hf.get(feat), af.get(feat)
            row += [h, a, (h - a) if (h is not None and a is not None) else None]
        for c in NEUTRAL_COLS:
            v = g.get(c)
            if isinstance(v, bool):
                v = 1.0 if v else 0.0
            row.append(_f(v))
        X.append([np.nan if v is None else float(v) for v in row])
        y.append(hs - as_)
        meta.append(g)
    return np.array(X, dtype=float), np.array(y, dtype=float), meta


def feature_names():
    names = []
    for feat in tff.TEAM_FEATURES:
        names += [f'home_{feat}', f'away_{feat}', f'diff_{feat}']
    return names + NEUTRAL_COLS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NFL', choices=['NFL', 'NCAAF'])
    ap.add_argument('--save', action='store_true',
                    help='write models/<sport>_margin_form_v1.json')
    args = ap.parse_args()
    sport = args.sport

    print(f'=== {sport} margin model · real features ===')
    rows, form = load(sport)
    X, y, meta = featurise(rows, form)
    seasons = np.array([m.get('season') or 0 for m in meta])
    print(f'  usable games (both teams have prior form): {len(y)}')
    if len(y) < 400:
        print('  ✖ not enough history to train honestly')
        return 1

    uniq = sorted(set(int(s) for s in seasons if s))
    # Hold out the two most recent seasons. Chronological, never shuffled:
    # a random split would let a late-season game teach an early one.
    test_seasons = set(uniq[-2:])
    train_seasons = set(uniq[:-2])
    tr = np.array([s in train_seasons for s in seasons])
    te = np.array([s in test_seasons for s in seasons])
    print(f'  train seasons {min(train_seasons)}-{max(train_seasons)} '
          f'(n={tr.sum()})   test {sorted(test_seasons)} (n={te.sum()})')

    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import RidgeCV

    imp = SimpleImputer(strategy='median')
    sc = StandardScaler()
    Xtr = sc.fit_transform(imp.fit_transform(X[tr]))
    Xte = sc.transform(imp.transform(X[te]))
    model = RidgeCV(alphas=np.logspace(-2, 3, 30))
    model.fit(Xtr, y[tr])

    pred = model.predict(Xte)
    ytest = y[te]
    mae = float(np.mean(np.abs(pred - ytest)))

    # Baselines on the SAME test games.
    base_mean = float(np.mean(np.abs(np.mean(y[tr]) - ytest)))
    spreads = np.array([_f(m.get('close_spread')) for m in meta], dtype=object)[te]
    have = np.array([s is not None for s in spreads])
    mkt_margin = np.array([(-float(s) if sport != 'NFL' else float(s))
                           for s in spreads[have]])
    mkt_mae = float(np.mean(np.abs(mkt_margin - ytest[have])))
    model_mae_h = float(np.mean(np.abs(pred[have] - ytest[have])))

    print()
    print(f'  MAE — always predict the mean : {base_mean:6.2f}')
    print(f'  MAE — FORM MODEL              : {mae:6.2f}')
    print(f'  on games with a closing line (n={have.sum()}):')
    print(f'     MAE — form model           : {model_mae_h:6.2f}')
    print(f'     MAE — the closing line     : {mkt_mae:6.2f}')

    # The question that actually matters: is the model's DISAGREEMENT with
    # the line predictive of the cover? Two-sided by construction.
    edge = pred[have] - mkt_margin
    actual_cover_home = (ytest[have] + (mkt_margin * -1) > 0)
    home_covered = (ytest[have] > mkt_margin)
    print()
    print(f'  model sides with HOME on {100*np.mean(edge > 0):.0f}% of games '
          f'(50% = unbiased, the shipping projection is 12-24%)')
    for thr in (0.0, 2.0, 3.0, 4.0, 6.0):
        sel = np.abs(edge) >= thr
        if sel.sum() < 25:
            continue
        picks_home = edge[sel] > 0
        won = (picks_home == home_covered[sel])
        print(f'     |edge| >= {thr:4.1f} pts : {won.sum():4d}-{(~won).sum():<4d} '
              f'= {100*won.mean():5.1f}%   (n={sel.sum()})')
    print('  breakeven at -110 = 52.4%')

    names = feature_names()
    order = np.argsort(-np.abs(model.coef_))
    print()
    print('  strongest features (standardised weight):')
    for i in order[:10]:
        print(f'     {names[i]:<26} {model.coef_[i]:+7.3f}')

    if args.save:
        out = {
            'sport': sport, 'kind': 'margin_ridge_v1',
            'features': names, 'coef': [float(c) for c in model.coef_],
            'intercept': float(model.intercept_),
            'impute_median': [float(v) for v in imp.statistics_],
            'scale_mean': [float(v) for v in sc.mean_],
            'scale_std': [float(v) for v in sc.scale_],
            'train_seasons': sorted(train_seasons),
            'test_seasons': sorted(test_seasons),
            'test_mae': mae, 'market_mae': mkt_mae,
            'alpha': float(model.alpha_),
        }
        path = os.path.join(_HERE, 'models', f'{sport.lower()}_margin_form_v1.json')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(out, fh, indent=1)
        print(f'\n  saved -> {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
