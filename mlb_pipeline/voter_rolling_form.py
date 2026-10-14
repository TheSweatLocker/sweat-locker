#!/usr/bin/env python3
"""The rolling-form panelist — a real equation over inputs no other model reads.

== 2026-10-14 - WHY THIS EXISTS ==
panelist_registry.py traced all 27 duplicate panelist pairs to their cause:
almost every "model" is the same number again. NFL `model` is `projected`'s own
intermediates, NFL `v3` is `projected` plus a rest nudge, NCAAF `sp_plus` is a
literal alias of `projected`, NCAAF `mc` simulates around `projected`, and
NHL's three nameplates all fall out of one Elo predictor.

Independence comes from different INPUTS, not different formulas. Measured on
our own models: their deviations from the line correlate +0.836 while their
levels correlate only +0.076 -- they are not three analysts who happen to
agree, they are one analyst's number passed through three transforms.

Nothing we own reads RECENT FORM to predict a score. 88 `team_form` registry
signals READ rolling stats; no equation turns rolling form into a projection.
That makes this the one genuinely new information set available, and it is why
this panelist can be a distinct voice where the others cannot.

== THE EQUATION ==
Fitted by ordinary least squares on feature_history, which is walk-forward and
truncation-tested, so the inputs carry no leak of their own:

  margin ~ 1 + (home_last5_margin - away_last5_margin)
             + (home_last3_margin - away_last3_margin)
             + ((home_pf_pg - home_pa_pg) - (away_pf_pg - away_pa_pg))

  total  ~ 1 + (home_pf_pg + home_pa_pg + away_pf_pg + away_pa_pg)
             + (home_last5_total + away_last5_total)
             + (home_avg_total + away_avg_total)

THERE IS DELIBERATELY NO MARKET TERM. Adding the closing line would improve
every accuracy number and destroy the only thing this panelist is for: an
opinion formed without looking at the line. A panelist that reads the market is
the market with extra steps.

== WHY THE BACKFILL IS HONEST ==
For each game the coefficients are refitted on games strictly EARLIER than that
game's date -- walk-forward, never once on the game being predicted. A single
fit over the whole history would hand every prediction the answer.

Backfilled rows are still written pregame=FALSE, because leak-free is not the
same as live: the equation itself was chosen with knowledge of the period it is
being scored on. They are valid for measuring the equation and must never be
reported as a live record. Only `--today` writes pregame=TRUE.

== WHAT IS NOT CLAIMED ==
This writes predictions and nothing else. It does not seat a panelist, enter a
consensus, or change a published pick. `build_panelist_records.py` grades it and
`audit_panelist_independence.py` decides whether it is a distinct voice; it
earns a seat in panelist_registry.py only if it passes both. Prior measurement
says to expect that in roughly one market of twelve (NHL total: slope +0.567,
CI[+0.202, +0.900], n=1,067), and to expect noise elsewhere.

USAGE
    python voter_rolling_form.py --backfill --sport NHL
    python voter_rolling_form.py --backfill            # all sports, walk-forward
    python voter_rolling_form.py --today               # live, pregame=TRUE
    python voter_rolling_form.py --backfill --dry-run
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

PANELIST = 'rolling_form'
MODEL_VERSION = 'rolling_form_v1.0.0'

RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'NHL': 'nhl_game_results', 'NBA': 'nba_game_results',
           'MLB': 'mlb_game_results', 'NCAAB': 'ncaab_game_results'}

#: Below this many strictly-earlier games we refuse to fit rather than emit a
#: prediction from almost no history.
MIN_TRAIN = 150

FEATS = {
    'margin': [
        ('form_margin_gap',
         lambda f: f['home_last5_margin'] - f['away_last5_margin']),
        ('recent_margin_gap',
         lambda f: f['home_last3_margin'] - f['away_last3_margin']),
        ('scoring_gap',
         lambda f: (f['home_pf_pg'] - f['home_pa_pg'])
                   - (f['away_pf_pg'] - f['away_pa_pg'])),
    ],
    'total': [
        ('combined_scoring',
         lambda f: f['home_pf_pg'] + f['home_pa_pg']
                   + f['away_pf_pg'] + f['away_pa_pg']),
        ('recent_totals',
         lambda f: f['home_last5_total'] + f['away_last5_total']),
        ('season_totals',
         lambda f: f['home_avg_total'] + f['away_avg_total']),
    ],
}
NEEDED = {
    'margin': ('home_last5_margin', 'away_last5_margin', 'home_last3_margin',
               'away_last3_margin', 'home_pf_pg', 'home_pa_pg',
               'away_pf_pg', 'away_pa_pg'),
    'total': ('home_pf_pg', 'home_pa_pg', 'away_pf_pg', 'away_pa_pg',
              'home_last5_total', 'away_last5_total',
              'home_avg_total', 'away_avg_total'),
}


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def page(table: str, params: dict) -> list[dict]:
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


def load(sport: str, market: str):
    """feature_history rows for this sport, with the result when it exists."""
    fh = page('feature_history', {'select': '*', 'sport': f'eq.{sport}'})
    res = {str(r.get('game_id')): r
           for r in page(RESULTS[sport], {'select': '*'})}
    rows = []
    for r in fh:
        f = r.get('features') or {}
        if isinstance(f, str):
            try:
                f = json.loads(f)
            except Exception:                                  # noqa: BLE001
                continue
        if any(f.get(k) is None for k in NEEDED[market]):
            continue
        gid = str(r.get('game_id'))
        g = res.get(gid)
        hs = _f((g or {}).get('home_score'))
        as_ = _f((g or {}).get('away_score'))
        y = None
        if hs is not None and as_ is not None:
            y = (hs - as_) if market == 'margin' else (hs + as_)
        rows.append({'game_id': gid, 'date': str(r.get('game_date'))[:10],
                     'f': f, 'y': y})
    rows.sort(key=lambda x: x['date'])
    return rows


def design(rows, specs):
    cols = [np.ones(len(rows))]
    for _n, fn in specs:
        cols.append(np.array([fn(r['f']) for r in rows], dtype=float))
    return np.column_stack(cols)


def fit(rows, specs):
    """OLS on rows that have a result. No market term, by design."""
    tr = [r for r in rows if r['y'] is not None]
    if len(tr) < MIN_TRAIN:
        return None
    beta, *_ = np.linalg.lstsq(design(tr, specs),
                               np.array([r['y'] for r in tr], dtype=float),
                               rcond=None)
    return beta


def predict(beta, row, specs):
    x = np.concatenate(([1.0], [fn(row['f']) for _n, fn in specs]))
    return float(x @ beta)


def upsert(payload: list[dict], dry: bool) -> int:
    if dry or not payload:
        return 0
    done = 0
    for i in range(0, len(payload), 500):
        chunk = payload[i:i + 500]
        r = requests.post(f'{SB}/rest/v1/panelist_predictions',
                          headers=H_WRITE,
                          params={'on_conflict':
                                  'sport,game_id,panelist,market'},
                          data=json.dumps(chunk), timeout=180)
        if r.status_code not in (200, 201, 204):
            print(f'    WRITE FAILED {r.status_code}: {r.text[:200]}')
            continue
        done += len(chunk)
    return done


def run(sport: str, market: str, mode: str, dry: bool) -> None:
    rows = load(sport, market)
    specs = FEATS[market]
    have_y = [r for r in rows if r['y'] is not None]
    print(f'\n{sport} - {market}: {len(rows)} rows with inputs, '
          f'{len(have_y)} with a result')
    if len(have_y) < MIN_TRAIN:
        print(f'  below MIN_TRAIN={MIN_TRAIN} — refusing to fit')
        return

    payload = []
    if mode == 'backfill':
        # Walk-forward: one refit per DATE, on games strictly earlier.
        by_date = defaultdict(list)
        for r in rows:
            by_date[r['date']].append(r)
        fitted = skipped = 0
        for d in sorted(by_date):
            train = [r for r in rows if r['date'] < d and r['y'] is not None]
            beta = fit(train, specs)
            if beta is None:
                skipped += len(by_date[d])
                continue
            fitted += 1
            for r in by_date[d]:
                payload.append({
                    'sport': sport, 'game_id': r['game_id'],
                    'game_date': r['date'], 'panelist': PANELIST,
                    'market': market,
                    'prediction': round(predict(beta, r, specs), 3),
                    'pregame': False,
                    'inputs': {k: r['f'].get(k) for k in NEEDED[market]},
                    'model_version': MODEL_VERSION,
                })
        print(f'  walk-forward: {fitted} refits, {len(payload)} predictions, '
              f'{skipped} skipped for thin history')
    else:
        # Live: fit on everything with a result, predict the unplayed games.
        beta = fit(rows, specs)
        if beta is None:
            print('  cannot fit')
            return
        todo = [r for r in rows if r['y'] is None]
        for r in todo:
            payload.append({
                'sport': sport, 'game_id': r['game_id'],
                'game_date': r['date'], 'panelist': PANELIST,
                'market': market,
                'prediction': round(predict(beta, r, specs), 3),
                'pregame': True,
                'inputs': {k: r['f'].get(k) for k in NEEDED[market]},
                'model_version': MODEL_VERSION,
            })
        print(f'  live: fitted on {len(have_y)}, {len(payload)} unplayed games')

    if payload:
        preds = [p['prediction'] for p in payload]
        print(f'  prediction spread: mean {np.mean(preds):+.2f} '
              f'sd {np.std(preds):.2f} range '
              f'[{min(preds):+.2f}, {max(preds):+.2f}]')
    wrote = upsert(payload, dry)
    print(f'  {"DRY RUN — would write" if dry else "wrote"} '
          f'{len(payload) if dry else wrote} rows')


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--backfill', action='store_true',
                   help='walk-forward over history, pregame=FALSE')
    g.add_argument('--today', action='store_true',
                   help='predict unplayed games, pregame=TRUE')
    ap.add_argument('--sport', choices=sorted(RESULTS))
    ap.add_argument('--market', choices=('margin', 'total'))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    mode = 'backfill' if a.backfill else 'today'
    sports = ([a.sport] if a.sport
              else ['NFL', 'NCAAF', 'NHL', 'NBA', 'MLB', 'NCAAB'])
    markets = [a.market] if a.market else ['margin', 'total']
    print(f'ROLLING-FORM PANELIST {MODEL_VERSION} — mode={mode}')
    print('No market term by design: a panelist that reads the line is the '
          'line with extra steps.')
    for s in sports:
        for m in markets:
            try:
                run(s, m, mode, a.dry_run)
            except Exception as e:                             # noqa: BLE001
                print(f'\n{s} - {m}: FAILED {type(e).__name__}: {str(e)[:140]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
