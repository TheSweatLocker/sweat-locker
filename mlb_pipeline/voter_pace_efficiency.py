#!/usr/bin/env python3
"""The pace-and-efficiency panelist — football's independent per-side base.

== 2026-10-14 - WHY THIS EXISTS ==
NCAAF's Monte Carlo is not a panelist. ncaaf_mc_simulator.py:314-331 takes
`projected_spread` and `projected_total` AS ARGUMENTS and sets

    base_home = (projected_total + projected_spread) / 2.0

A symmetric simulation around a point estimate returns that point estimate, so
mc_expected_margin converges to projected_spread and the two agree on side
99.6% of the time. The simulator's ADJUSTMENT machinery (weather, returning
production, rest, QB1 out) is genuinely its own and worth keeping -- it is only
the BASE that is an echo.

This builds the replacement base from inputs the other football panelists do
not use this way: each side's own recent scoring environment, its offensive
efficiency, the opponent's defensive efficiency allowed, and turnover margin.
`projected` is an SP+ power gap; `epa` is a net-EPA difference times a
constant; neither builds PER-SIDE points from pace plus matchup.

THE RULE THAT KEEPS IT INDEPENDENT: when an input is missing there is NO
FALLBACK to projected_spread or projected_total. A missing input means no
prediction. Falling back to the other model is precisely how the echo got
established, and a fallback that fires on 28% of games would reintroduce it.

== WHAT WAS MEASURED BEFORE ANY EQUATION WAS WRITTEN (NCAAF, n=462) ==
Sign and scale verified against ACTUAL points, because guessing which side a
column indexes is a documented way to ship a backwards model:

    away_def_epa_pp  -> home points   slope +25.96  corr +0.208
    home_off_epa_pp  -> home points   slope +19.76  corr +0.132
    home_l4_total_avg-> home points   slope  +0.41  corr +0.100
    away_to_margin   -> home points   slope  +0.73  corr +0.109

`def_epa_pp` is EPA ALLOWED: higher means a worse defense, confirmed by the
positive slope on the opponent's points.

TWO WARNINGS THE SAME MEASUREMENT PRODUCED, both honoured below:
 1. These inputs are WEAK -- 1.5 to 2.9 points per standard deviation. This
    panelist is being built because it is a DIFFERENT voice, not because it is
    expected to be a strong one.
 2. NCAAF home points average 35.08 against 18.88 away, an implied home
    advantage of +16.2 points. That is not home-field advantage, it is big
    programs hosting FCS opponents. So no equation here uses the population
    mean as a baseline; each side's level comes from its OWN recent scoring
    environment, and the home term is fitted rather than assumed.

== THE EQUATION ==
One regression over TEAM-GAMES (two rows per game, home and away), so the
model is symmetric by construction and cannot learn a different equation for
each side:

    team_points ~ 1 + own_recent_env + own_off_eff + opp_def_eff_allowed
                    + turnover_margin_diff + is_home

margin = predicted_home - predicted_away, total = their sum. Coefficients are
refitted walk-forward on strictly-earlier games, so a backfilled record never
sees its own game.

NOTHING IS WIRED. This writes to panelist_predictions. It replaces the NCAAF MC
base only after build_panelist_records.py and audit_panelist_independence.py
both pass it -- distinct voice AND better than noise.

USAGE
    python voter_pace_efficiency.py --backfill --sport NCAAF
    python voter_pace_efficiency.py --backfill
    python voter_pace_efficiency.py --today
    python voter_pace_efficiency.py --backfill --dry-run
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

PANELIST = 'pace_eff'
MODEL_VERSION = 'pace_eff_v1.0.0'
#: Minimum TEAM-GAME rows (two per game) before we will fit at all.
#: 150 rows = 75 games. NFL's whole graded season is 79 games, so a 200-row
#: floor refused NFL outright for the season; 150 rows against NFL's 4
#: parameters (intercept, off_eff, opp_def, is_home) is ~38 rows per
#: parameter, which is thin but fittable. NFL's record will be small-n and the
#: CI is what decides whether it earns a seat -- the threshold exists to stop
#: a fit on almost nothing, not to guarantee a usable one.
MIN_TRAIN = 150

CONTEXT = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context'}
RESULTS = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results'}

#: Per sport: how to read one side's inputs. Each returns a float or None.
#: `own` is the team whose points we are predicting; `opp` the other side.
#: Verified against a live select=* on 2026-10-14.
#:
#: FEATURES ARE PER SPORT, not a fixed list, because "no fallback" and "one
#: feature set for every sport" cannot both hold. NFL has no l4_total_avg and
#: no to_margin; its nearest recent-environment column, h2h_last5_avg_total,
#: is populated on only 123 of 300 rows, so demanding it dropped NFL to 107
#: games and the fit was refused. Carrying a sparse column costs two thirds of
#: the sample to buy one weak feature (recent_env corr +0.100), so NFL simply
#: does not have that feature. Fewer features on more games beats more
#: features on a third of them -- and it is still an independent equation,
#: because independence comes from off_eff/opp_def being per-side points
#: rather than a power gap.
SPEC = {
    'NCAAF': {
        'recent_env': lambda c, own, opp: _f(c.get(f'{own}_l4_total_avg')),
        'off_eff':    lambda c, own, opp: _f(c.get(f'{own}_off_epa_pp')),
        'opp_def':    lambda c, own, opp: _f(c.get(f'{opp}_def_epa_pp')),
        'to_diff':    lambda c, own, opp: _diff(c, f'{own}_to_margin',
                                                f'{opp}_to_margin'),
    },
    'NFL': {
        'off_eff':    lambda c, own, opp: _add(c, f'{own}_pass_epa_pg',
                                               f'{own}_rush_epa_pg'),
        'opp_def':    lambda c, own, opp: _add(c, f'{opp}_def_pass_epa_allowed',
                                               f'{opp}_def_rush_epa_allowed'),
    },
}
#: Feature order per sport — the keys of SPEC[sport], in a fixed order so the
#: coefficient vector is stable across runs.
FEATS = {'NCAAF': ('recent_env', 'off_eff', 'opp_def', 'to_diff'),
         'NFL':   ('off_eff', 'opp_def')}


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _add(c, a, b):
    x, y = _f(c.get(a)), _f(c.get(b))
    return None if x is None or y is None else x + y


def _diff(c, a, b):
    x, y = _f(c.get(a)), _f(c.get(b))
    return None if x is None or y is None else x - y


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


def load(sport: str):
    """One entry per game: both sides' feature vectors plus the result."""
    ctx = page(CONTEXT[sport], {'select': '*'})
    res = page(RESULTS[sport], {'select': '*'})
    by_id, by_key = {}, {}
    for r in res:
        if r.get('game_id'):
            by_id[str(r['game_id'])] = r
        by_key[(str(r.get('game_date'))[:10], str(r.get('home_team')),
                str(r.get('away_team')))] = r
    spec, order = SPEC[sport], FEATS[sport]
    out = []
    for c in ctx:
        hv = [spec[k](c, 'home', 'away') for k in order]
        av = [spec[k](c, 'away', 'home') for k in order]
        # NO FALLBACK: a missing input means no prediction for this game.
        if any(v is None for v in hv) or any(v is None for v in av):
            continue
        g = (by_id.get(str(c.get('game_id')))
             or by_key.get((str(c.get('game_date'))[:10],
                            str(c.get('home_team')), str(c.get('away_team')))))
        hs = _f((g or {}).get('home_score'))
        as_ = _f((g or {}).get('away_score'))
        out.append({'game_id': str(c.get('game_id')),
                    'date': str(c.get('game_date'))[:10],
                    'hv': hv, 'av': av, 'hs': hs, 'as': as_})
    out.sort(key=lambda x: x['date'])
    return out


def _rows_for_fit(games):
    """Team-game design: two rows per game, is_home last. Symmetric by build."""
    X, y = [], []
    for g in games:
        if g['hs'] is None or g['as'] is None:
            continue
        X.append([1.0] + list(g['hv']) + [1.0])
        y.append(g['hs'])
        X.append([1.0] + list(g['av']) + [0.0])
        y.append(g['as'])
    return np.array(X, dtype=float), np.array(y, dtype=float)


def fit(games):
    X, y = _rows_for_fit(games)
    if len(y) < MIN_TRAIN:
        return None
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def predict(beta, g):
    h = float(np.array([1.0] + list(g['hv']) + [1.0]) @ beta)
    a = float(np.array([1.0] + list(g['av']) + [0.0]) @ beta)
    return h, a


def upsert(payload, dry):
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


def _emit(sport, g, h, a, pregame):
    order = FEATS[sport]
    inputs = {k: v for k, v in zip(order, g['hv'])}
    inputs.update({f'opp_{k}': v for k, v in zip(order, g['av'])})
    base = {'sport': sport, 'game_id': g['game_id'], 'game_date': g['date'],
            'panelist': PANELIST, 'pregame': pregame, 'inputs': inputs,
            'model_version': MODEL_VERSION}
    return [dict(base, market='margin', prediction=round(h - a, 3)),
            dict(base, market='total', prediction=round(h + a, 3))]


def run(sport: str, mode: str, dry: bool) -> None:
    games = load(sport)
    graded = [g for g in games if g['hs'] is not None and g['as'] is not None]
    print(f'\n{sport}: {len(games)} games with ALL inputs present '
          f'(no fallback), {len(graded)} graded')
    if len(graded) * 2 < MIN_TRAIN:
        print(f'  below MIN_TRAIN={MIN_TRAIN} team-game rows — refusing to fit')
        return

    payload = []
    if mode == 'backfill':
        by_date = defaultdict(list)
        for g in games:
            by_date[g['date']].append(g)
        fits = skipped = 0
        for d in sorted(by_date):
            train = [g for g in graded if g['date'] < d]
            beta = fit(train)
            if beta is None:
                skipped += len(by_date[d])
                continue
            fits += 1
            for g in by_date[d]:
                h, a = predict(beta, g)
                payload += _emit(sport, g, h, a, False)
        print(f'  walk-forward: {fits} refits, {len(payload)//2} games, '
              f'{skipped} skipped for thin history')
        beta_last = fit(graded)
        if beta_last is not None:
            names = ('intercept',) + FEATS[sport] + ('is_home',)
            print('  final coefficients (fitted on all graded, for reading '
                  'only):')
            for n, b in zip(names, beta_last):
                print(f'    {n:<12}{b:+9.3f}')
    else:
        beta = fit(graded)
        if beta is None:
            print('  cannot fit')
            return
        todo = [g for g in games if g['hs'] is None or g['as'] is None]
        for g in todo:
            h, a = predict(beta, g)
            payload += _emit(sport, g, h, a, True)
        print(f'  live: fitted on {len(graded)} games, '
              f'{len(payload)//2} unplayed')

    if payload:
        for mk in ('margin', 'total'):
            v = [p['prediction'] for p in payload if p['market'] == mk]
            if v:
                print(f'  {mk:<7} mean {np.mean(v):+7.2f} sd {np.std(v):6.2f} '
                      f'range [{min(v):+.1f}, {max(v):+.1f}]')
    wrote = upsert(payload, dry)
    print(f'  {"DRY RUN — would write" if dry else "wrote"} '
          f'{len(payload) if dry else wrote} rows')


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--backfill', action='store_true')
    g.add_argument('--today', action='store_true')
    ap.add_argument('--sport', choices=sorted(CONTEXT))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    mode = 'backfill' if a.backfill else 'today'
    sports = [a.sport] if a.sport else ['NCAAF', 'NFL']
    print(f'PACE-AND-EFFICIENCY PANELIST {MODEL_VERSION} — mode={mode}')
    print('Per-side points from pace + matchup. No fallback to projected: a '
          'missing input means no prediction.')
    for s in sports:
        try:
            run(s, mode, a.dry_run)
        except Exception as e:                                 # noqa: BLE001
            print(f'\n{s}: FAILED {type(e).__name__}: {str(e)[:140]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
