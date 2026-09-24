"""A margin model that can actually pick an NFL spread.

2026-09-24. Andy, after rejecting a dog-flip rule on sight: "leave it
until we work scorer and how to get to recommending good spreads based on
good data."

The reason the old path could not pick a side, measured rather than
assumed: model_pred_*_points projects a SMALLER favourite margin than the
market in 22 of 30 settled 2026 games (mean -1.21, median -2.15), so
"take the side the model likes" degenerates into "take the dog" and went
12-18 ATS (40.0%).

That is a calibration failure. A margin model is only useful for spreads
if its errors are centred and its scale matches reality, so this builds
one on real history and reports whether it clears the market — with no
credit for hindsight.

Data: nfl_game_results, 7,550 games 1999-2026, closing spread + result on
every one (backfilled from nflverse 2026-09-23).

Method, deliberately boring:
  * Elo-style team rating updated game by game in chronological order.
    A team's rating entering a game uses only games already played, so
    there is no leakage by construction.
  * Season carryover regresses ratings toward the mean, because rosters
    turn over.
  * Margin prediction = (rating difference + home field) / points-per-Elo.
  * Home field and the Elo scale are FIT ON TRAINING SEASONS ONLY and
    then frozen for the test seasons.

The only number that matters at the end is ATS hit rate on unseen
seasons, bucketed by how big an edge the model claims. A model that beats
52.4% at a usable volume is worth wiring into the scorer. One that does
not is worth knowing about before it picks anything.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
for _l in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

BREAKEVEN = 0.5238          # -110 both sides


def page(table, **params):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=180,
                         params={'limit': 1000, 'offset': off, **params})
        b = r.json()
        if not isinstance(b, list):
            raise SystemExit(f'{table}: {str(b)[:300]}')
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def load_games() -> list[dict]:
    rows = page('nfl_game_results',
                select='game_id,season,week,game_date,away_team,home_team,'
                       'home_score,away_score,close_spread,game_type')
    out = []
    for g in rows:
        if g.get('home_score') is None or g.get('away_score') is None:
            continue
        if not g.get('game_date'):
            continue
        out.append(g)
    out.sort(key=lambda g: (str(g['game_date']), str(g['game_id'])))
    return out


def run_elo(games, k=20.0, carry=0.75, hfa_elo=55.0):
    """Walk the schedule once, recording each game's PRE-game ratings."""
    rating = defaultdict(lambda: 1500.0)
    season_seen = None
    recorded = []
    for g in games:
        if g['season'] != season_seen:
            if season_seen is not None:
                for t in list(rating):
                    rating[t] = 1500.0 + (rating[t] - 1500.0) * carry
            season_seen = g['season']
        h, a = g['home_team'], g['away_team']
        rh, ra = rating[h], rating[a]
        recorded.append({**g, 'elo_home': rh, 'elo_away': ra,
                         'elo_diff': (rh - ra) + hfa_elo})
        margin = g['home_score'] - g['away_score']
        exp_h = 1.0 / (1.0 + 10 ** (-((rh - ra) + hfa_elo) / 400.0))
        act_h = 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)
        mult = ((abs(margin) + 3.0) ** 0.8) / (7.5 + 0.006 * abs((rh - ra) + hfa_elo))
        delta = k * mult * (act_h - exp_h)
        rating[h] += delta
        rating[a] -= delta
    return recorded


def fit_scale(rows) -> tuple[float, float]:
    """Least-squares fit of actual margin on elo_diff: margin = a*elo + b."""
    xs = [r['elo_diff'] for r in rows]
    ys = [r['home_score'] - r['away_score'] for r in rows]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    a = num / den if den else 0.0
    return a, my - a * mx


def evaluate(rows, a, b, label: str, buckets=(0, 1, 2, 3, 4, 6)):
    """ATS record bucketed by claimed edge.

    Market convention, verified rather than assumed: regressing actual home
    margin on close_spread gives slope +1.04 in BOTH the 1999-2019 rows I
    backfilled from nflverse and the 2020+ rows written by nfl_odds_pull.
    So a positive close_spread means the HOME team is favoured and the
    market's expected home margin is +close_spread.

    Getting this backwards is not a subtle error. The first run of this
    model used -close_spread and reported 77.9% ATS out of sample, which is
    not a result, it is a sign bug announcing itself.
    """
    res = defaultdict(lambda: {'w': 0, 'l': 0, 'p': 0})
    for r in rows:
        sp = r.get('close_spread')
        if sp is None:
            continue
        pred_home = a * r['elo_diff'] + b
        mkt_home = float(sp)
        edge = pred_home - mkt_home
        actual = r['home_score'] - r['away_score']
        if abs(actual - mkt_home) < 1e-9:
            verdict = 'p'
        elif (edge > 0) == (actual > mkt_home):
            verdict = 'w'
        else:
            verdict = 'l'
        for lo in buckets:
            if abs(edge) >= lo:
                res[lo][verdict] += 1
    print(f'\n--- {label}   (n={len(rows)} games)')
    print(f"{'edge >=':>8s} {'W-L-P':>14s} {'hit':>7s} {'n':>6s} {'vs 52.4%':>9s}")
    for lo in buckets:
        c = res[lo]
        tot = c['w'] + c['l']
        if tot < 25:
            continue
        hit = c['w'] / tot
        print(f"{lo:>8} {c['w']}-{c['l']}-{c['p']:<5} {hit*100:>6.1f}% {tot:>6d} "
              f"{(hit-BREAKEVEN)*100:>+8.1f}pp")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--train-through', type=int, default=2021,
                    help='last season used for fitting (default 2021)')
    args = ap.parse_args()

    games = load_games()
    print(f'games with a final score: {len(games)}  '
          f'({games[0]["season"]}-{games[-1]["season"]})')
    rows = run_elo(games)
    priced = [r for r in rows if r.get('close_spread') is not None]
    print(f'of those, priced: {len(priced)}')

    train = [r for r in priced if r['season'] <= args.train_through]
    test = [r for r in priced if r['season'] > args.train_through]
    print(f'train <= {args.train_through}: {len(train)}   test: {len(test)}')

    a, b = fit_scale(train)
    print(f'\nfit on TRAIN only: margin = {a:.4f} * elo_diff {b:+.3f}'
          f'   ({1/a:.1f} Elo points per point of margin)')

    # sanity: is the model's margin centred against the market, unlike
    # model_pred_*_points which ran -1.21 mean?
    d = [(a * r['elo_diff'] + b) - float(r['close_spread']) for r in test]
    print(f'test-set (model margin - market margin): mean {sum(d)/len(d):+.2f}')

    evaluate(train, a, b, 'IN-SAMPLE (train seasons — expect flattery)')
    evaluate(test, a, b, f'OUT-OF-SAMPLE ({args.train_through+1}+)')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
