#!/usr/bin/env python3
"""Does an NBA side/total model beat the closing line? Walk-forward only.

WHY (2026-10-04)
----------------
NBA opens 10-21 and we serve nothing. Before building an engine, the question
that decides whether an engine is worth building has to be answered honestly:
does our projection carry information the market has not already priced?

This is the same test that killed the NHL prop scorer and the NFL adjEPA bet
(margin ~ 1.08*market + (-0.015)*adjEPA, ATS 49.4% on n=3,981). Both looked
plausible and both lost to the price. Running the test first is cheaper than
shipping a losing surface and discovering it in the record.

METHOD — no leakage, by construction
------------------------------------
Games are processed in date order. For every game, the rating used to predict
it is fit ONLY on games that finished strictly earlier. Nothing about the game
being predicted, and nothing from later in the season, touches its own
projection. The first MIN_HISTORY games of the sample are used for fitting
only and never scored.

Two models, each measured against the closing number:

  SIDE   projected margin = SRS(home) - SRS(away) + HFA
         edge = projected margin - market margin, where market margin is
         -close_spread (close_spread is the HOME handicap in NBA, verified
         1198/1198 against stored grades)

  TOTAL  projected total = league mean + pace/scoring deviation of both
         teams, fit the same walk-forward way
         edge = projected total - close_total

WHAT COUNTS AS AN EDGE
----------------------
Not direction accuracy — PRICE. A side bet at -110 needs 52.4% to break even,
so every bucket is reported with its ROI at the real juice as well as its hit
rate, and with its n. A bucket that hits 54% on n=40 is noise; the output
shows n everywhere so it cannot be read as more than it is.

The regression at the bottom is the sharpest version of the question: with the
market margin already in the model, does our projection get a non-zero
coefficient? If it does not, the projection is redundant and the engine should
not be built on it.

    python nba_model_validate.py
    python nba_model_validate.py --min-edge 3
"""
from __future__ import annotations
import argparse, collections, os, statistics, sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

from compute_margin_strength import (SPORT_CFG, load_games, clean_games,
                                     fit_srs)

MIN_HISTORY = 200        # games fit before any prediction is scored
JUICE = -110             # standard side/total price
BREAKEVEN = 110 / 210    # 0.5238


def _roi(w: int, l: int, push: int = 0) -> float:
    """Units per unit risked at -110."""
    risk = w + l
    if not risk:
        return 0.0
    won = w * (100 / 110) - l
    return won / risk


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    return sxy / ((sxx * syy) ** 0.5) if sxx and syy else 0.0


def _ols2(y, x1, x2):
    """y ~ a + b1*x1 + b2*x2 by normal equations; returns (b1, b2)."""
    n = len(y)
    if n < 10:
        return 0.0, 0.0
    m1, m2, my = sum(x1) / n, sum(x2) / n, sum(y) / n
    a1 = [v - m1 for v in x1]
    a2 = [v - m2 for v in x2]
    ay = [v - my for v in y]
    s11 = sum(v * v for v in a1)
    s22 = sum(v * v for v in a2)
    s12 = sum(p * q for p, q in zip(a1, a2))
    s1y = sum(p * q for p, q in zip(a1, ay))
    s2y = sum(p * q for p, q in zip(a2, ay))
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-9:
        return 0.0, 0.0
    return (s22 * s1y - s12 * s2y) / det, (s11 * s2y - s12 * s1y) / det


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-edge', type=float, default=0.0)
    args = ap.parse_args()

    cfg = SPORT_CFG['NBA']
    games = clean_games('NBA', load_games('NBA'))
    games = [g for g in games if g.get('date') and g.get('margin') is not None]
    games.sort(key=lambda g: g['date'])
    print(f'\n=== NBA model validation · {len(games)} clean graded games ===')
    print(f'    hfa={cfg["hfa"]} cap={cfg["cap"]} ridge={cfg["ridge"]} · '
          f'walk-forward, {MIN_HISTORY}-game burn-in\n')

    # Pull totals alongside, keyed the same way as the games we loaded.
    SB, KEY = os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY']
    H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
    tot_by = {}
    off = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/nba_game_results', headers=H, timeout=120,
                         params={'select': 'game_date,home_team,away_team,'
                                           'close_total,total_points',
                                 'limit': 1000, 'offset': off})
        if r.status_code not in (200, 206):
            break
        b = r.json()
        for x in b:
            tot_by[(str(x['game_date']), x['home_team'], x['away_team'])] = x
        if len(b) < 1000:
            break
        off += 1000

    # ---- walk forward ------------------------------------------------
    side_rows, tot_rows = [], []
    # Refit every REFIT games rather than every game: the rating moves slowly
    # and refitting 2,600 times is pure cost. Still strictly backward-looking.
    REFIT = 10
    rating = {}
    pts_for = collections.defaultdict(list)      # team -> points scored
    pts_against = collections.defaultdict(list)  # team -> points allowed
    for i, g in enumerate(games):
        if i >= MIN_HISTORY and (i - MIN_HISTORY) % REFIT == 0:
            rating = fit_srs(games[:i], cfg)
        if i >= MIN_HISTORY and rating:
            h, a = g['home'], g['away']
            if h in rating and a in rating and g.get('home_line') is not None:
                proj = rating[h] - rating[a] + cfg['hfa']
                mkt = -float(g['home_line'])     # market's expected home margin
                side_rows.append({'proj': proj, 'mkt': mkt,
                                  'actual': float(g['margin']),
                                  'line': float(g['home_line'])})
            # TOTALS. First cut projected ~112 against a ~228 market — it
            # summed league-relative deviations and dropped the league level,
            # so almost every game fell outside the edge buckets and the
            # "result" was 102 games of nonsense. A total is an offence
            # against a defence, twice over:
            #
            #   proj = (home scores-for + away allows)/2
            #        + (away scores-for + home allows)/2
            rec = tot_by.get((str(g['date']), h, a))
            if (rec and rec.get('close_total') is not None
                    and len(pts_for[h]) >= 10 and len(pts_for[a]) >= 10
                    and len(pts_against[h]) >= 10 and len(pts_against[a]) >= 10):
                hf = statistics.mean(pts_for[h][-20:])
                ha = statistics.mean(pts_against[h][-20:])
                af = statistics.mean(pts_for[a][-20:])
                aa = statistics.mean(pts_against[a][-20:])
                proj_t = (hf + aa) / 2.0 + (af + ha) / 2.0
                tot_rows.append({'proj': proj_t,
                                 'mkt': float(rec['close_total']),
                                 'actual': float(rec['total_points'])
                                 if rec.get('total_points') is not None else None})
        # update scoring history AFTER predicting (no leakage)
        rec = tot_by.get((str(g['date']), g['home'], g['away']))
        if rec and rec.get('total_points') is not None:
            half = float(rec['total_points']) / 2.0
            hp = half + g['margin'] / 2.0      # home points
            ap = half - g['margin'] / 2.0      # away points
            pts_for[g['home']].append(hp)
            pts_against[g['home']].append(ap)
            pts_for[g['away']].append(ap)
            pts_against[g['away']].append(hp)

    print(f'scored predictions: {len(side_rows)} sides, '
          f'{len([t for t in tot_rows if t["actual"] is not None])} totals\n')

    # ---- SIDE --------------------------------------------------------
    print('--- SIDES ---')
    if side_rows:
        pm = [r['proj'] for r in side_rows]
        mk = [r['mkt'] for r in side_rows]
        ac = [r['actual'] for r in side_rows]
        print(f'  corr(projection, actual margin) = {_corr(pm, ac):+.3f}')
        print(f'  corr(market,     actual margin) = {_corr(mk, ac):+.3f}')
        print(f'  corr(projection, market)        = {_corr(pm, mk):+.3f}')
        b1, b2 = _ols2(ac, mk, pm)
        print(f'\n  actual_margin ~ {b1:+.3f}*market {b2:+.3f}*projection')
        print('  (a projection that adds nothing over the price gets b2 ~ 0)')

        print(f'\n  ATS by |edge| bucket, bet the side our projection favours:')
        print('  %-12s %6s %6s %6s %6s %8s %9s' %
              ('edge', 'n', 'W', 'L', 'push', 'hit%', 'ROI@-110'))
        buckets = [(0, 2), (2, 4), (4, 6), (6, 99)]
        for lo, hi in buckets:
            w = l = p = 0
            for r in side_rows:
                e = r['proj'] - r['mkt']
                if not (lo <= abs(e) < hi):
                    continue
                # back home if our projection is above the market's margin
                adj = (r['actual'] + r['line']) if e > 0 else -(r['actual'] + r['line'])
                if abs(adj) < 1e-9:
                    p += 1
                elif adj > 0:
                    w += 1
                else:
                    l += 1
            if w + l + p == 0:
                continue
            hit = 100 * w / (w + l) if w + l else 0
            print('  %-12s %6d %6d %6d %6d %7.1f%% %+8.1f%%' %
                  (f'{lo}-{hi if hi < 99 else "+"}', w + l + p, w, l, p,
                   hit, 100 * _roi(w, l)))
        w = sum(1 for r in side_rows
                if (r['actual'] + r['line']) * (1 if r['proj'] > r['mkt'] else -1) > 1e-9)
        l = sum(1 for r in side_rows
                if (r['actual'] + r['line']) * (1 if r['proj'] > r['mkt'] else -1) < -1e-9)
        print('  %-12s %6d %6d %6d %6s %7.1f%% %+8.1f%%  <- all, breakeven 52.4%%' %
              ('ALL', len(side_rows), w, l, len(side_rows) - w - l,
               100 * w / max(1, w + l), 100 * _roi(w, l)))

    # ---- TOTALS ------------------------------------------------------
    print('\n--- TOTALS ---')
    tr = [t for t in tot_rows if t['actual'] is not None]
    if tr:
        pm = [r['proj'] for r in tr]
        mk = [r['mkt'] for r in tr]
        ac = [r['actual'] for r in tr]
        print(f'  corr(projection, actual total) = {_corr(pm, ac):+.3f}')
        print(f'  corr(market,     actual total) = {_corr(mk, ac):+.3f}')
        b1, b2 = _ols2(ac, mk, pm)
        print(f'  actual_total ~ {b1:+.3f}*market {b2:+.3f}*projection')
        print('\n  %-12s %6s %6s %6s %6s %8s %9s' %
              ('edge', 'n', 'W', 'L', 'push', 'hit%', 'ROI@-110'))
        for lo, hi in [(0, 3), (3, 6), (6, 10), (10, 99)]:
            w = l = p = 0
            for r in tr:
                e = r['proj'] - r['mkt']
                if not (lo <= abs(e) < hi):
                    continue
                if abs(r['actual'] - r['mkt']) < 1e-9:
                    p += 1
                elif (r['actual'] > r['mkt']) == (e > 0):
                    w += 1
                else:
                    l += 1
            if w + l + p == 0:
                continue
            print('  %-12s %6d %6d %6d %6d %7.1f%% %+8.1f%%' %
                  (f'{lo}-{hi if hi < 99 else "+"}', w + l + p, w, l, p,
                   100 * w / max(1, w + l), 100 * _roi(w, l)))
    else:
        print('  no scored totals')
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
