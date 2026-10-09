"""A real model panel for NCAAF, built per-lens from raw per-game data.

WHY (2026-10-09)
----------------
Andy, correcting me and rightly: "we cant just give up on football predcitions
what kind of thought proces is that? THe product is an engine, which includes
having a take and picking spreads and props."

He is right, and my error was specific: I generalised three failures on thin
samples (NFL n=20 card plays, NCAAF n=67) into a verdict on football modelling.
The honest reading of the day is narrower and more useful —

    pick level   247 graded picks vs ~229 signals  -> cannot learn here
    game level   3,550 team-games vs ~488 params   -> CAN learn here

Every failure was at the pick level. The one credible result (an
opponent-adjusted rating) was at the game level. And NCAAF's real structural
problem has been known for days: it has ONE model. MLB, which returns +8.1%
ROI on card plays, has four that disagree with each other usefully.

WHAT THIS BUILDS
One opponent-adjusted lens per raw metric, each solved independently:

    points              scoring
    off_ppa             efficiency, the EPA family
    off_success_rate    consistency — how often a play works at all
    off_explosiveness   big-play rate
    yards_per_pass      passing efficiency
    yards_per_rush      rushing efficiency

These are different QUESTIONS about a team, not one question asked six ways.
That distinction is the whole point: NFL's v3 and v4 correlate at r=+0.9989
and r is a constant respectively, so its "three model panel" is one opinion.
This measures the pairwise correlation of every lens so that cannot happen
silently again.

HOW A LENS BECOMES A SPREAD PREDICTION
A rating in success-rate units cannot be compared to a point spread, so each
lens gets a two-parameter scaling fitted ON TRAINING DATA ONLY:

    margin ~= a + b * (rating_home - rating_away + home_adv)

One slope and one intercept per lens. Then the prediction is in points and can
be held against the closing line.

DISCIPLINE
  * walk-forward throughout. Ratings AND the scaling are fitted on games
    strictly before the predicted date. Nothing from the predicted week
    informs its own prediction.
  * every lens is reported with n and a two-standard-error band, and judged
    against the 52.38% breakeven, not against a coin flip.
  * a lens only earns a place in the panel if it clears the bar ALONE. The
    combination is reported too, but a combination that works only because one
    strong lens carries five dead ones is not a panel.

This is v1 and deliberately does not yet include injuries, weather, pace or
rest. Those are the next inputs, not a reason to withhold the panel.

WRITES NOTHING.

CLI
    python ncaaf_lens_panel.py
    python ncaaf_lens_panel.py --min-train 600
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML
from opponent_adjusted_rating import _page, _f, fit

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
BREAKEVEN = 52.38

LENSES = ('points', 'off_ppa', 'off_success_rate', 'off_explosiveness',
          'yards_per_pass', 'yards_per_rush')


def load_all() -> list[dict]:
    """Team-games carrying every lens metric, with a home flag."""
    sel = ('team,opponent,game_date,season,points,opp_points,off_ppa,'
           'off_success_rate,off_explosiveness,yards_per_pass,yards_per_rush')
    raw = _page('ncaaf_team_game_stats', {'select': sel})
    home_of = {}
    for g in _page('ncaaf_game_results',
                   {'select': 'game_date,home_team,away_team'}):
        home_of[(str(g['game_date'])[:10], str(g['home_team']),
                 str(g['away_team']))] = True
    out = []
    for r in raw:
        d = str(r.get('game_date'))[:10]
        t, o = str(r.get('team')), str(r.get('opponent'))
        if (d, t, o) in home_of:
            is_home = 1.0
        elif (d, o, t) in home_of:
            is_home = 0.0
        else:
            continue
        vals = {m: _f(r.get(m)) for m in LENSES}
        p, q = _f(r.get('points')), _f(r.get('opp_points'))
        if p is None or q is None or any(v is None for v in vals.values()):
            continue
        vals['points'] = p
        out.append({'date': d, 'team': t, 'opp': o, 'is_home': is_home,
                    'season': int(r.get('season') or 0),
                    'margin': p - q, 'vals': vals})
    out.sort(key=lambda z: z['date'])
    return out


def lens_ratings(games, metric, season):
    """off/def/home for one metric, via the shared ridge solver."""
    shaped = [{'team': g['team'], 'opp': g['opp'], 'is_home': g['is_home'],
               'season': g['season'], 'perf': g['vals'][metric],
               'date': g['date']} for g in games]
    return fit(shaped, season)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--min-train', type=int, default=800, dest='min_train')
    ap.add_argument('--min-games', type=int, default=3, dest='min_games')
    a = ap.parse_args()

    games = load_all()
    print(f'=== NCAAF lens panel · {len(games)} team-games with ALL lenses · '
          f'{games[0]["date"]}..{games[-1]["date"]}')
    print(f'    lenses: {", ".join(LENSES)}')

    res = collections.defaultdict(list)
    for g in _page('ncaaf_game_results',
                   {'select': 'game_date,home_team,away_team,close_spread,'
                              'spread_result',
                    'game_date': f'gte.{a.season}-08-01'}):
        if str(g.get('spread_result') or '').lower() not in (
                'home_covered', 'away_covered'):
            continue
        if _f(g.get('close_spread')) is None:
            continue
        res[str(g['game_date'])[:10]].append(g)

    # walk forward: one refit per game date
    preds = collections.defaultdict(list)       # lens -> [(pred, mkt, covered)]
    for d in sorted(res):
        prior = [x for x in games if x['date'] < d]
        if len(prior) < a.min_train:
            continue
        seen = collections.Counter(x['team'] for x in prior)
        models = {}
        for m in LENSES:
            off, dfn, home = lens_ratings(prior, m, a.season)
            rating = {t: off.get(t, 0.0) + dfn.get(t, 0.0) for t in off}
            # scale this lens to points, on TRAIN only
            xs, ys = [], []
            for g in prior:
                if g['is_home'] != 1.0:
                    continue
                dv = (rating.get(g['team'], 0.0)
                      - rating.get(g['opp'], 0.0) + home)
                xs.append(dv)
                ys.append(g['margin'])
            if len(xs) < 100:
                continue
            A = np.vstack([np.asarray(xs), np.ones(len(xs))]).T
            b, c = np.linalg.lstsq(A, np.asarray(ys), rcond=None)[0]
            models[m] = (rating, home, float(b), float(c))

        for g in res[d]:
            h, aw = str(g['home_team']), str(g['away_team'])
            if seen[h] < a.min_games or seen[aw] < a.min_games:
                continue
            covered = str(g['spread_result']).lower() == 'home_covered'
            mkt = -_f(g['close_spread'])          # market's expected home margin
            for m, (rating, home, b, c) in models.items():
                if h not in rating or aw not in rating:
                    continue
                dv = rating[h] - rating[aw] + home
                preds[m].append((b * dv + c, mkt, covered))

    print(f'\n  WALK-FORWARD, per lens (fit only on earlier games)')
    print(f'    {"lens":<20}{"n":>5}{"hit%":>8}{"2SE":>7}  verdict')
    survivors = []
    for m in LENSES:
        rows = preds.get(m) or []
        if len(rows) < 80:
            print(f'    {m:<20}{len(rows):>5}  too few')
            continue
        w = sum(1 for p, mk, cov in rows if (p > mk) == cov)
        n = len(rows)
        pct = w / n * 100
        se = (0.5 / n ** 0.5) * 100
        beats = pct - 2 * se > BREAKEVEN
        if beats:
            survivors.append(m)
        print(f'    {m:<20}{n:>5}{pct:>7.1f}%{2 * se:>6.1f}  '
              f'{"BEATS breakeven" if beats else "no"}')

    # independence — the check NFL's v3/v4 never got
    print(f'\n  LENS INDEPENDENCE (pairwise r of the point predictions)')
    common = min((len(preds[m]) for m in LENSES if preds.get(m)), default=0)
    if common >= 80:
        series = {m: [p for p, _mk, _c in preds[m][:common]] for m in LENSES
                  if len(preds.get(m) or []) >= common}
        keys = sorted(series)
        for i, x in enumerate(keys):
            for y in keys[i + 1:]:
                try:
                    r_ = statistics.correlation(series[x], series[y])
                except Exception:                          # noqa: BLE001
                    continue
                flag = '   <-- DUPLICATE, not a second opinion' \
                    if abs(r_) > 0.97 else ''
                print(f'    {x:<20}vs {y:<20}r={r_:+.4f}{flag}')

    print(f'\n  {len(survivors)} lens(es) beat breakeven alone: '
          f'{survivors or "NONE"}')
    if not survivors:
        print('    => no single lens is a standalone edge yet. That is a')
        print('       result about THESE SIX INPUTS on THIS much data, not')
        print('       about football. Next inputs: injuries, rest, weather,')
        print('       pace, and the market-structure signals that already')
        print('       measure positive.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
