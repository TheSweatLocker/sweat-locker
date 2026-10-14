#!/usr/bin/env python3
"""A rolling-form voter for every sport — fitted, validated, and reported honestly.

══ 2026-10-14 · WHY THIS EXISTS ══
Andy's engine vision lists "rolling live stats" as one of the panelists. We have
88 `team_form` signals that READ rolling stats, but **no equation that turns
rolling form into a projected score**. NFL's projection uses season-level off/def
indices; nothing anywhere uses recent form to predict points.

That makes a rolling voter the one genuinely NEW panelist available, because
independence comes from different INPUTS, not different formulas — we measured
model deviations correlating +0.836 while their levels showed only +0.076. Four
distinct sheets for football would be: season margins (SRS), per-play efficiency
(EPA), RECENT FORM (this), and simulation (MC).

The inputs already exist, leak-free: `feature_history` computes pf_pg, pa_pg,
last3_margin, last5_margin, last5_total and avg_total for all six sports,
walk-forward and truncation-tested.

══ THE DISCIPLINE THIS SCRIPT ENFORCES ON ITSELF ══
NFL's existing total projection is the cautionary tale: it scores respectably on
MAE and its deviations from the line are ANTI-informative (slope −0.908,
CI[−1.553, −0.242]). MAE alone would have passed it. So every model here reports
THREE numbers, and a model is only interesting if it passes all three:

  1. MAE vs the market          — are we accurate?
  2. sd of our deviation        — do we disagree at all?
  3. slope of the residual on   — is the disagreement worth anything?
     that deviation               (>0 informative, ~0 noise, <0 backwards)

And it is fitted on a TRAIN slice and scored on a TEST slice, by time. The NFL
totals work showed an in-sample gain of +0.479 MAE evaporate to −0.154 under
leave-one-out and −1.949 on a temporal split, so in-sample numbers are reported
as the overfit ceiling and never as a result.

NOTHING IS WIRED BY THIS SCRIPT. It answers "is this panelist worth having"
before anyone changes the engine.

USAGE
    python build_rolling_voter.py                 # every sport, both markets
    python build_rolling_voter.py --sport NFL
    python build_rolling_voter.py --market total
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys

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
#: close_spread is the HOME handicap everywhere EXCEPT NFL, where it is the AWAY
#: line (project_close_spread_sign_bug_914, verified n=7,325). NHL has no
#: close_spread at all — the puck line IS its spread.
SPREAD_COL = {'NHL': 'close_puckline'}
TRAIN_FRAC = 0.6
MIN_ROWS = 120


def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
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


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


FEATS = {
    'spread': [
        ('form_margin_gap', lambda f: f['home_last5_margin'] - f['away_last5_margin']),
        ('recent_margin_gap', lambda f: f['home_last3_margin'] - f['away_last3_margin']),
        ('scoring_gap', lambda f: (f['home_pf_pg'] - f['home_pa_pg'])
                                 - (f['away_pf_pg'] - f['away_pa_pg'])),
    ],
    'total': [
        ('combined_scoring', lambda f: f['home_pf_pg'] + f['home_pa_pg']
                                      + f['away_pf_pg'] + f['away_pa_pg']),
        ('recent_totals', lambda f: f['home_last5_total'] + f['away_last5_total']),
        ('season_totals', lambda f: f['home_avg_total'] + f['away_avg_total']),
    ],
}
NEEDED = {
    'spread': ('home_last5_margin', 'away_last5_margin', 'home_last3_margin',
               'away_last3_margin', 'home_pf_pg', 'home_pa_pg',
               'away_pf_pg', 'away_pa_pg'),
    'total': ('home_pf_pg', 'home_pa_pg', 'away_pf_pg', 'away_pa_pg',
              'home_last5_total', 'away_last5_total',
              'home_avg_total', 'away_avg_total'),
}


def load(sport: str, market: str):
    fh = page('feature_history', {'select': '*', 'sport': f'eq.{sport}'})
    if not fh:
        return []
    res = {str(r.get('game_id')): r for r in page(RESULTS[sport], {'select': '*'})}
    scol = SPREAD_COL.get(sport, 'close_spread')
    rows = []
    for r in fh:
        g = res.get(str(r.get('game_id')))
        if not g:
            continue
        hs, as_ = _f(g.get('home_score')), _f(g.get('away_score'))
        if hs is None or as_ is None:
            continue
        f = r.get('features') or {}
        if any(f.get(k) is None for k in NEEDED[market]):
            continue
        if market == 'spread':
            cs = _f(g.get(scol))
            if cs is None:
                continue
            # one place for the sign rule, as in compute_margin_strength
            home_line = -cs if sport == 'NFL' else cs
            # market's implied home margin is the negated handicap
            rows.append({'date': str(r['game_date'])[:10], 'f': f,
                         'y': hs - as_, 'mkt': -home_line})
        else:
            ct = _f(g.get('close_total'))
            if ct is None:
                continue
            rows.append({'date': str(r['game_date'])[:10], 'f': f,
                         'y': hs + as_, 'mkt': ct})
    rows.sort(key=lambda x: x['date'])
    return rows


def boot_slope(x, y, n=1200):
    rnd = random.Random(17)
    xa, ya = np.asarray(x), np.asarray(y)
    k = len(x)
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


def run(sport: str, market: str) -> None:
    rows = load(sport, market)
    print(f'\n{"="*72}\n{sport} · {market.upper()}   rows with inputs + result + market: '
          f'{len(rows)}')
    if len(rows) < MIN_ROWS:
        print(f'  below MIN_ROWS={MIN_ROWS} — refusing to fit')
        return
    cut = int(len(rows) * TRAIN_FRAC)
    tr, te = rows[:cut], rows[cut:]
    print(f'  temporal split at {rows[cut]["date"]}: train {len(tr)}  test {len(te)}')
    specs = FEATS[market]

    def design(S, with_market):
        cols = [np.ones(len(S))]
        if with_market:
            cols.append(np.array([r['mkt'] for r in S]))
        for _n, fn in specs:
            cols.append(np.array([fn(r['f']) for r in S]))
        return np.column_stack(cols)

    y_tr = np.array([r['y'] for r in tr])
    y_te = np.array([r['y'] for r in te])
    mkt_te = np.array([r['mkt'] for r in te])

    def mae(p, y=y_te):
        return float(np.mean(np.abs(y - p)))

    print(f'  actual mean {y_te.mean():+.2f} sd {y_te.std():.2f}')
    print(f'  {"model":<26}{"MAE":>8}{"dev sd":>9}{"slope":>9}   CI              verdict')
    print(f'  {"market (as-is)":<26}{mae(mkt_te):>8.3f}{"—":>9}{"—":>9}')

    for label, with_market in (('rolling form ALONE', False),
                               ('market + rolling form', True)):
        Xtr, Xte = design(tr, with_market), design(te, with_market)
        beta, *_ = np.linalg.lstsq(Xtr, y_tr, rcond=None)
        p = Xte @ beta
        dev = p - mkt_te                     # how much we disagree with the line
        resid = y_te - mkt_te                # what the line missed
        if dev.std() < 1e-9:
            print(f'  {label:<26}{mae(p):>8.3f}{dev.std():>9.3f}'
                  f'{"n/a":>9}   (never disagrees)')
            continue
        sl = float(np.polyfit(dev, resid, 1)[0])
        lo, hi = boot_slope(list(dev), list(resid))
        cis = f'[{lo:+.3f}, {hi:+.3f}]' if lo is not None else 'n/a'
        if lo is not None and lo > 0:
            v = 'INFORMATIVE'
        elif hi is not None and hi < 0:
            v = 'BACKWARDS'
        else:
            v = 'noise'
        print(f'  {label:<26}{mae(p):>8.3f}{dev.std():>9.3f}{sl:>+9.3f}   '
              f'{cis:<16}{v}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(RESULTS))
    ap.add_argument('--market', choices=('spread', 'total'))
    a = ap.parse_args()
    sports = [a.sport] if a.sport else ['NFL', 'NCAAF', 'MLB', 'NHL', 'NBA', 'NCAAB']
    markets = [a.market] if a.market else ['spread', 'total']
    print('ROLLING-FORM VOTER · fitted on TRAIN, scored on TEST, by time.')
    print('A model is only interesting if it is accurate AND disagrees AND its '
          'disagreements are informative.')
    for s in sports:
        for m in markets:
            try:
                run(s, m)
            except Exception as e:                             # noqa: BLE001
                print(f'\n{s} · {m}: FAILED {type(e).__name__}: {str(e)[:120]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
