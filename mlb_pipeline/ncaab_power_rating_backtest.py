# -*- coding: utf-8 -*-
"""A POINT-IN-TIME NCAAB power rating built from our own results, and what it
is worth against the real stored prices.

== WHY THIS EXISTS (2026-11-27) =======================================
ANDY: "we have the gold standard of college basketball data (kenpom), not
being able to do data anayltics to find a better answer is lazy and
probably flat out wrong. Stop worrying about beating the close, and worry
about overall results based on engine driven signals (money flow,
siutaionals records, matchup gaps or ineeficeincies)".

He is right that the earlier work was lazy, and here is the specific way.
The 2026-11-25 sweep that concluded "no stat differential beats the close"
ran MEDIAN SPLITS over whatever columns the context tables happened to
fill. For NCAAB that table is 1,195 rows, all current-season. It never
built a rating, never touched the 12,880 settled results, and a median
split on a differential is the weakest available form of the question.

KenPom itself cannot answer it either, which is worth stating plainly
because it looks like it should:
  * ncaab_game_results.home_adj_oe/_de/_em fill 1,014 rows, ALL 2026-27.
    There is no KenPom history in the database.
  * _kenpom_cache_2024-25.json holds a 364-team season-FINAL 2024-25
    snapshot. A season-final rating applied to a game from that season
    CONTAINS that game's result. Using it as a feature is the same leak
    that put 1,030 of 1,583 context team-rows under
    test_trend_enrich_no_leak -- and it would read as an enormous edge.
  * kenpom_cache holds 2026 + 2027 ratings and per-day fanmatch only.

So the honest way to use 12,880 games is to build the rating ourselves,
point-in-time, from the scores we own. Points are all we have (no
possessions, no FGA/TO/FTA on the results table), so this is an
opponent-adjusted MARGIN rating -- ridge least squares, the classic Massey
form -- and not a true per-100-possession efficiency. Two models:

    margin:  r[home] - r[away] + hca   = home_score - away_score
    total:   t[home] + t[away] + base  = home_score + away_score

solved on games STRICTLY BEFORE each game's date, so walk-forward is
structural rather than bookkept. XtX accumulates incrementally and is
re-solved once per date (~250 solves of a ~350x350 system).

Season handling: ratings reset each season, optionally seeded with the
prior season's final rating as ridge pseudo-observations (--prior-weight).
Rosters turn over, so the seed is deliberately weak; without any seed
November is unratable and we reproduce the seasonality hole from
feedback_eligibility_piles_into_the_unpriced_season.

== WHAT "OVERALL RESULTS" MEANS HERE, AND THE ONE LIMIT ==============
The headline is W-L and UNITS at the stored price -- actual P&L, not
closing-line value. The controls (always-home, always-favourite, over,
under, on the SAME games) stay, and they are NOT a CLV test: they answer
"does this beat a one-word rule I could follow without an engine at all".
A signal that cannot beat that is not a signal.

The real limit: open_spread / open_total fill 1,038 / 1,043 rows, all
2026-27. So "we bet the morning number, not the close" is NOT testable on
history -- the close is the only price 2024-25 and 2025-26 carry. That is
a data gap to start fixing, not a result.

NCAAB stores no spread/total juice, so ATS and totals are priced at -110
(0.909 per unit won); moneylines use the stored close through the same
+/-100 guard as refit_signal_registry.

    python ncaab_power_rating_backtest.py
    python ncaab_power_rating_backtest.py --prior-weight 0 --min-games 8
"""
from __future__ import annotations

import argparse
import math
import pickle
import sys
from collections import defaultdict
from statistics import NormalDist

import numpy as np

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                              # noqa: BLE001
    pass

PKL = '_ncaab_hist.pkl'
ATS_WIN = 100.0 / 110.0          # -110, the assumed NCAAB spread/total price
ATS_BE = 110.0 / 210.0           # 52.38%


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ml_payout(odds):
    """Units won per 1 risked. A PLUS price pays odds/100, not 100/odds."""
    o = fnum(odds)
    if o is None or -100.0 < o < 100.0:   # not an American price at all
        return None
    return (100.0 / -o) if o < 0 else (o / 100.0)


def ml_implied(odds):
    o = fnum(odds)
    if o is None or -100.0 < o < 100.0:
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


# ---------------------------------------------------------------- reporting
def tally(picks):
    w = sum(1 for o, _ in picks if o == 'W')
    l = sum(1 for o, _ in picks if o == 'L')
    p = sum(1 for o, _ in picks if o == 'P')
    u = sum(x for _, x in picks)
    n = w + l
    return dict(n=n, w=w, l=l, p=p, u=u,
                hit=(w / n) if n else 0.0,
                roi=(100.0 * u / n) if n else 0.0)


def se_vs(hit, n, be):
    if n < 1:
        return 0.0
    s = math.sqrt(be * (1 - be) / n)
    return (hit - be) / s if s else 0.0


def line(label, picks, be=ATS_BE, floor=30):
    t = tally(picks)
    if t['n'] == 0:
        print(f'  {label:<34} --')
        return t
    z = se_vs(t['hit'], t['n'], be)
    flag = '' if t['n'] >= floor else f'  (n<{floor}, no verdict)'
    print(f'  {label:<34} n={t["n"]:>5}  {t["w"]:>4}-{t["l"]:<4} '
          f'{100 * t["hit"]:>5.1f}%  {t["u"]:+8.2f}u  '
          f'ROI {t["roi"]:+6.2f}%  {z:+5.2f} SE{flag}')
    return t


def days(a, b):
    return int((np.datetime64(a) - np.datetime64(b)).astype(int))


# ------------------------------------------------------------------ ratings
def build(rows, min_games, prior_weight, ridge):
    """Walk forward by date; emit one feature dict per rateable game."""
    rows = [r for r in rows
            if r['home_score'] is not None and r['away_score'] is not None
            and r['home_team'] and r['away_team'] and r['game_date']]
    rows.sort(key=lambda r: (r['game_date'], r['game_id']))

    teams = sorted({t for r in rows for t in (r['home_team'], r['away_team'])})
    idx = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    print(f'teams {T}  games {len(rows)}  '
          f'dates {len({r["game_date"] for r in rows})}')

    by_season = defaultdict(list)
    for r in rows:
        by_season[r['season'] or '?'].append(r)

    out = []
    prior_rating = {}

    for season in sorted(by_season):
        g = by_season[season]
        M = np.zeros((T + 1, T + 1)); mv = np.zeros(T + 1)
        S = np.zeros((T + 1, T + 1)); sv = np.zeros(T + 1)
        for i in range(T):
            M[i, i] += ridge
            S[i, i] += ridge
        seeded = 0
        if prior_weight > 0 and prior_rating:
            for t, v in prior_rating.items():
                i = idx[t]
                M[i, i] += prior_weight
                mv[i] += prior_weight * v
                seeded += 1
        nprior = defaultdict(int)
        last_game = {}
        played = defaultdict(list)
        beat_by = defaultdict(dict)
        streak_away = defaultdict(int)

        byd = defaultdict(list)
        for r in g:
            byd[r['game_date']].append(r)
        dates = sorted(byd)

        total_games = 0
        emitted = 0
        # Walk-forward de-bias. The raw rating predicts home margin ~4 pts
        # HIGH, because hca is fitted on a pool that includes early-season
        # buy games (huge home margins against teams that barely appear)
        # and then applied to conference play. A CONSTANT OFFSET READS LIKE
        # AN EDGE: at +4 the gap points HOME on 4,823 games and AWAY on 691,
        # so "back our side" is "back the home team" wearing a rating.
        # Corrected from the residual mean over games ALREADY SETTLED on
        # earlier dates only -- never this game's own result.
        bsum = bn = tsum = tn = 0.0
        for d in dates:
            rate = None
            if total_games >= 200:
                try:
                    rate = (np.linalg.solve(M, mv), np.linalg.solve(S, sv))
                except np.linalg.LinAlgError:
                    rate = None

            mbias = (bsum / bn) if bn >= 50 else 0.0
            tbias = (tsum / tn) if tn >= 50 else 0.0
            todays = []
            for r in byd[d]:
                h, a = r['home_team'], r['away_team']
                if rate is None or nprior[h] < min_games \
                        or nprior[a] < min_games:
                    continue
                rm, rs = rate
                hi, ai = idx[h], idx[a]
                out.append(dict(
                    mbias=mbias, tbias=tbias,
                    gid=r['game_id'], date=d, season=season, home=h, away=a,
                    pm=float(rm[hi] - rm[ai] + rm[T]),
                    pt=float(rs[hi] + rs[ai] + rs[T]),
                    hr=float(rm[hi]), ar=float(rm[ai]), hca=float(rm[T]),
                    margin=r['home_score'] - r['away_score'],
                    total=r['home_score'] + r['away_score'],
                    cs=fnum(r['close_spread']), ct=fnum(r['close_total']),
                    hml=r['home_ml_close'], aml=r['away_ml_close'],
                    sr=r['spread_result'], tr=r['total_result'],
                    hw=r['home_win'],
                    hrest=(days(d, last_game[h][0]) if h in last_game
                           else None),
                    arest=(days(d, last_game[a][0]) if a in last_game
                           else None),
                    hp5=sum(1 for x in played[h] if 0 < days(d, x) <= 5),
                    ap5=sum(1 for x in played[a] if 0 < days(d, x) <= 5),
                    h_away_streak=streak_away[h],
                    a_away_streak=streak_away[a],
                    h_prev_margin=(last_game[h][1] if h in last_game
                                   else None),
                    a_prev_margin=(last_game[a][1] if a in last_game
                                   else None),
                    h_revenge=beat_by[h].get(a),
                    a_revenge=beat_by[a].get(h),
                    ngh=nprior[h], nga=nprior[a],
                ))
                todays.append(out[-1])
                emitted += 1

            # Fold today's residuals in only AFTER emitting, so the offset a
            # game is corrected by never contains that game.
            for o in todays:
                bsum += o['pm'] - o['margin']
                bn += 1
                tsum += o['pt'] - o['total']
                tn += 1

            for r in byd[d]:
                h, a = r['home_team'], r['away_team']
                hi, ai = idx[h], idx[a]
                mg = r['home_score'] - r['away_score']
                tp = r['home_score'] + r['away_score']
                for i, si in ((hi, 1.0), (ai, -1.0), (T, 1.0)):
                    for j, sj in ((hi, 1.0), (ai, -1.0), (T, 1.0)):
                        M[i, j] += si * sj
                    mv[i] += si * mg
                for i in (hi, ai, T):
                    for j in (hi, ai, T):
                        S[i, j] += 1.0
                    sv[i] += tp
                nprior[h] += 1
                nprior[a] += 1
                total_games += 1
                last_game[h] = (d, mg)
                last_game[a] = (d, -mg)
                played[h].append(d)
                played[a].append(d)
                beat_by[h][a] = mg
                beat_by[a][h] = -mg
                streak_away[a] += 1
                streak_away[h] = 0

        try:
            rm = np.linalg.solve(M, mv)
            prior_rating = {t: float(rm[idx[t]]) for t in teams
                            if nprior[t] >= 10}
        except np.linalg.LinAlgError:
            prior_rating = {}
        print(f'  {season}: {len(g):>5} games, {emitted:>5} rateable '
              f'(seeded {seeded} teams from prior season)')
    return out


# -------------------------------------------------------------------- picks
def ats_pick(o, side):
    if o['sr'] == 'push':
        return ('P', 0.0)
    if o['sr'] not in ('home_covered', 'away_covered'):
        return None
    won = (o['sr'] == 'home_covered') if side == 'H' \
        else (o['sr'] == 'away_covered')
    return ('W', ATS_WIN) if won else ('L', -1.0)


def tot_pick(o, side):
    if o['tr'] == 'push':
        return ('P', 0.0)
    if o['tr'] not in ('over', 'under'):
        return None
    won = (o['tr'] == 'over') if side == 'O' else (o['tr'] == 'under')
    return ('W', ATS_WIN) if won else ('L', -1.0)


def ml_pick(o, side):
    if o['hw'] is None:
        return None
    pay = ml_payout(o['hml'] if side == 'H' else o['aml'])
    if pay is None:
        return None
    won = o['hw'] if side == 'H' else (not o['hw'])
    return ('W', pay) if won else ('L', -1.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-games', type=int, default=6)
    ap.add_argument('--prior-weight', type=float, default=4.0)
    ap.add_argument('--ridge', type=float, default=3.0)
    a = ap.parse_args()

    rows = pickle.load(open(PKL, 'rb'))
    print('=== point-in-time NCAAB power rating ===')
    print(f'min prior games {a.min_games}  prior seed weight '
          f'{a.prior_weight}  ridge {a.ridge}\n')
    feats = build(rows, a.min_games, a.prior_weight, a.ridge)

    priced = [o for o in feats
              if o['cs'] is not None
              and o['sr'] in ('home_covered', 'away_covered', 'push')]
    tp = [o for o in feats
          if o['ct'] is not None and o['tr'] in ('over', 'under', 'push')]
    print(f'\nrateable {len(feats)}; priced+graded ATS {len(priced)}, '
          f'totals {len(tp)}')
    per = defaultdict(int)
    for o in priced:
        per[o['season']] += 1
    print('  ATS sample by season: '
          + ', '.join(f'{k} {v}' for k, v in sorted(per.items())))

    raw = np.array([o['pm'] for o in priced])
    ours = np.array([o['pm'] - o['mbias'] for o in priced])
    mkt = np.array([-o['cs'] for o in priced])
    act = np.array([o['margin'] for o in priced])
    print('\n--- rating quality (predicting MARGIN, before any price) ---')
    print(f'  n={len(priced)}')
    print(f'  ours RAW  MAE {np.abs(raw - act).mean():6.2f}  '
          f'corr {np.corrcoef(raw, act)[0, 1]:+.3f}  '
          f'bias {(raw - act).mean():+.2f}')
    print(f'  ours ADJ  MAE {np.abs(ours - act).mean():6.2f}  '
          f'corr {np.corrcoef(ours, act)[0, 1]:+.3f}  '
          f'bias {(ours - act).mean():+.2f}')
    print(f'  market    MAE {np.abs(mkt - act).mean():6.2f}  '
          f'corr {np.corrcoef(mkt, act)[0, 1]:+.3f}  '
          f'bias {(mkt - act).mean():+.2f}')
    sigma = float((act - mkt).std())
    print(f'  market residual sd {sigma:.2f} (sigma for the ML win prob)')

    # THE TEST A CONSTANT OFFSET CANNOT FAKE. Both sides are measured as
    # departures from the market line, so any fixed bias in our rating
    # cancels out of the correlation. If our disagreement with the line
    # carries no information about how the game departs from the line,
    # this is zero and no threshold on the gap can be profitable.
    print('\n--- does our rating know anything the line does not? ---')
    gap = ours - mkt
    res = act - mkt
    r = float(np.corrcoef(gap, res)[0, 1])
    n = len(gap)
    tstat = r * math.sqrt(max(n - 2, 1)) / math.sqrt(max(1 - r * r, 1e-12))
    print(f'  corr(our gap vs line, actual vs line) = {r:+.4f}  '
          f'n={n}  t={tstat:+.2f}')
    X = np.column_stack([np.ones(n), mkt, ours])
    beta, *_ = np.linalg.lstsq(X, act, rcond=None)
    yh = X @ beta
    ss = float(((act - yh) ** 2).sum())
    sig2 = ss / (n - 3)
    cov = sig2 * np.linalg.inv(X.T @ X)
    print(f'  OLS actual ~ 1 + line + ours:   line {beta[1]:+.3f} '
          f'(t {beta[1] / math.sqrt(cov[1, 1]):+.1f}),   '
          f'ours {beta[2]:+.3f} (t {beta[2] / math.sqrt(cov[2, 2]):+.1f})')
    print('    a line coefficient near 1 with ours near 0 means the market '
          'already contains our rating.')
    need = ATS_BE - 0.5
    print(f'  for reference, a {need:.2%} edge over 50% is what -110 needs; '
          f'gap sd is {gap.std():.2f} pts')

    print('\n--- controls: one-word rules on these same games ---')
    line('always HOME ATS',
         [p for p in (ats_pick(o, 'H') for o in priced) if p])
    line('always FAVOURITE ATS',
         [p for p in (ats_pick(o, 'H' if o['cs'] < 0 else 'A')
                      for o in priced) if p])
    line('always DOG ATS',
         [p for p in (ats_pick(o, 'A' if o['cs'] < 0 else 'H')
                      for o in priced) if p])
    line('always OVER', [p for p in (tot_pick(o, 'O') for o in tp) if p])
    line('always UNDER', [p for p in (tot_pick(o, 'U') for o in tp) if p])

    print('\n--- MATCHUP GAP: our predicted margin minus the market line ---')
    print('    positive => we rate HOME better than the number does')
    for lo in (1, 2, 3, 4, 5, 6, 8, 10):
        pk = []
        for o in priced:
            d = (o['pm'] - o['mbias']) + o['cs']
            if abs(d) < lo:
                continue
            p = ats_pick(o, 'H' if d > 0 else 'A')
            if p:
                pk.append(p)
        line(f'|gap| >= {lo:<2} pts, back our side', pk)
    print('    split by which side the gap points at:')
    for lo in (3, 5, 8):
        for side, want in (('HOME', 1), ('AWAY', -1)):
            pk = []
            for o in priced:
                d = (o['pm'] - o['mbias']) + o['cs']
                if abs(d) < lo or (d > 0) != (want > 0):
                    continue
                p = ats_pick(o, 'H' if want > 0 else 'A')
                if p:
                    pk.append(p)
            line(f'|gap| >= {lo}, points {side}', pk)

    print('\n--- TOTALS GAP: our predicted total minus the closing total ---')
    for lo in (2, 4, 6, 8, 10):
        pk = []
        for o in tp:
            d = (o['pt'] - o['tbias']) - o['ct']
            if abs(d) < lo:
                continue
            p = tot_pick(o, 'O' if d > 0 else 'U')
            if p:
                pk.append(p)
        line(f'|gap| >= {lo:<2}, back our side', pk)

    print('\n--- ML VALUE: our win prob vs the stored closing price ---')
    nd = NormalDist(0, sigma)
    for edge in (0.02, 0.04, 0.06, 0.08, 0.10):
        pk = []
        for o in priced:
            ph = nd.cdf(o['pm'] - o['mbias'])
            for side, p_ours, odds in (('H', ph, o['hml']),
                                       ('A', 1 - ph, o['aml'])):
                imp = ml_implied(odds)
                if imp is None or p_ours - imp < edge:
                    continue
                q = ml_pick(o, side)
                if q:
                    pk.append(q)
        line(f'ML edge >= {edge:.0%}', pk, be=0.5)

    print('\n--- SITUATIONALS (derived from results, point-in-time) ---')

    def sit(label, pred, side):
        pk = []
        for o in priced:
            if not pred(o):
                continue
            p = ats_pick(o, side)
            if p:
                pk.append(p)
        line(label, pk)

    sit('home on 1 day rest, back AWAY', lambda o: o['hrest'] == 1, 'A')
    sit('away on 1 day rest, back HOME', lambda o: o['arest'] == 1, 'H')
    sit('home rest adv >= 3d, back HOME',
        lambda o: None not in (o['hrest'], o['arest'])
        and o['hrest'] - o['arest'] >= 3, 'H')
    sit('away rest adv >= 3d, back AWAY',
        lambda o: None not in (o['hrest'], o['arest'])
        and o['arest'] - o['hrest'] >= 3, 'A')
    sit('away 3-in-5, back HOME', lambda o: o['ap5'] >= 2, 'H')
    sit('home 3-in-5, back AWAY', lambda o: o['hp5'] >= 2, 'A')
    sit('away 3rd+ straight road, back HOME',
        lambda o: o['a_away_streak'] >= 3, 'H')
    sit('away 5th+ straight road, back HOME',
        lambda o: o['a_away_streak'] >= 5, 'H')
    sit('home off a 20+ loss, back HOME',
        lambda o: o['h_prev_margin'] is not None
        and o['h_prev_margin'] <= -20, 'H')
    sit('away off a 20+ loss, back HOME',
        lambda o: o['a_prev_margin'] is not None
        and o['a_prev_margin'] <= -20, 'H')
    sit('home off a 20+ win, back HOME',
        lambda o: o['h_prev_margin'] is not None
        and o['h_prev_margin'] >= 20, 'H')
    sit('home revenge (lost to opp), back HOME',
        lambda o: o['h_revenge'] is not None and o['h_revenge'] < 0, 'H')
    sit('away revenge (lost to opp), back AWAY',
        lambda o: o['a_revenge'] is not None and o['a_revenge'] < 0, 'A')
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
