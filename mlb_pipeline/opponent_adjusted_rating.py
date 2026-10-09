"""Opponent-adjusted team ratings, computed from our own raw per-game data.

ANDY'S VISION (2026-10-09), and it is the right one
---------------------------------------------------
"SOR needs to be more than just record, it should take into account opponents
they faced offensive record (rolling) so if team a beat team b week one but
then team b goes on a 5 week stretch of wins after showcasing a top stat
defense and great pass game per data, the SOR and SOS of team a should reflect
that team b is now pretty good so that win is even more impressive for team a
... why is sor/sos solid, because we pull raw data and compute ourselves."

That example IS the algorithm. A team's rating depends on its opponents'
ratings, which depend on theirs, so you solve the whole league at once and a
past win gets more valuable as the beaten team proves itself. This is what SP+
and the SRS family do, and it is strictly better than what we have:

    CURRENT  SOS = mean of opponents' shrunk WIN%, head-to-head removed
             SOR = own win% - what an average team would have won
             Measured 2026-10-09: SOS has ZERO relationship to covering
             (NCAAF r=-0.002 n=274, NFL r=-0.017 n=60), and that null is
             trustworthy because the test was LEAKY and leakage can only
             inflate. Win% throws away margin, efficiency and who you played
             it against.

    THIS     performance is modelled per game as
                 perf(i vs j) = off_i - def_j + home_adv
             and all off_/def_ ratings are solved simultaneously by ridge
             least squares. Opponent strength is then the opponent's RATING,
             not its record, so quality propagates through the schedule.

WHY THIS IS WELL-POWERED WHERE THE PICK-LEVEL WORK WAS NOT
Earlier today a learned pick-ranking score collapsed out of sample (+0.246 ->
-0.197) because there were 247 graded picks against ~229 signals — fewer
observations than parameters. Here 3,550 team-games survive the home/away join (of 4,486 with scores),
against ~488 parameters — an offence and a defence rating per team — across
2024-2026. The raw layer is roughly fourteen times richer than the pick layer,
which is exactly Andy's point about computing from raw data.

THE BAR THIS HAS TO CLEAR, AND IT IS NOT "DOES IT RANK TEAMS SENSIBLY"
Any competent rating ranks teams sensibly; that proves nothing about betting.
The market already prices team quality, and this project has a standing
measurement that no side or total model beats the close
(project_models_dont_beat_the_close_1005). So the test is walk-forward:

    fit on games BEFORE week W  ->  predict games IN week W
    does (our expected margin - the market line) predict who covered?

Nothing from week W informs the ratings used to predict week W. That is the
discipline every leaky backtest in this repo skipped
(project_sp_plus_backtests_are_leaky_926).

RIDGE, and why the penalty is not optional
A team with two games would otherwise get an extreme rating from noise — the
same failure that put Duquesne at SOR -1.000 on a two-game sample in the
win%-based version. The penalty pulls thin-sample teams toward average and
fades as games accumulate. LAMBDA is a round number on purpose; fitting it on
this data would be the overfitting this file exists to avoid.

RESULT, measured 2026-10-09 — READ THIS BEFORE USING IT
As a RATING it works. Top of the 2026 table came out Indiana +16.3, Ohio State
+16.1, Miami +15.1, Georgia +14.2, Notre Dame +14.1; bottom Charlotte -12.1,
Kent State -8.6. Offence and defence separate properly (Ohio State off +7.07 /
def +8.99). SOS now reflects opponent QUALITY rather than record — hardest
schedules Texas, Mississippi State, Northwestern — and Andy's retroactive
property falls straight out: beat a team that later rates well and your own
rating rises, because your rating is a function of theirs.

As a BETTING EDGE it does not clear the bar:

    WALK-FORWARD, 277 games predicted from prior games only
      back our side every game   141-136   50.9%  +/-6.0pp (2SE)
      only when |edge| >= 7       93-102   47.7%  +/-7.2pp
      breakeven 52.38%

It does not beat the close, and it is WORST exactly where it disagrees with
the market most — the signature of a model whose extremes are miscalibrated
rather than insightful. One bucket ("we like home by 3-7 more", 69.4% on
n=36) looks good and is not a finding: five buckets were examined and its
error bar is +/-16.7pp.

This replicates project_models_dont_beat_the_close_1005 on independent data,
which is the honest reading: team quality is table stakes, not an edge. SP+ and
DVOA are public and the line already contains them. So this belongs as CONTEXT
(a better SOS/SOR than win% for the game-detail card) and NOT as a pick signal.

WRITES NOTHING. Reporting and validation only.

CLI
    python opponent_adjusted_rating.py                       # points margin
    python opponent_adjusted_rating.py --metric off_ppa      # efficiency
    python opponent_adjusted_rating.py --season 2026 --top 15
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

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H

#: Ridge penalty. Round by choice, not fitted.
LAMBDA = 8.0
#: Prior seasons are real information but stale; weight them down rather than
#: discarding them, so early-season ratings are not built on three games.
SEASON_DECAY = {0: 1.0, 1: 0.35, 2: 0.12}


def _page(t, p, cap=200000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:130]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_games(metric: str) -> list[dict]:
    """One row per team-game, with a home flag joined from the results table."""
    sel = ('team,opponent,game_date,season,week,points,opp_points,'
           'off_ppa,off_success_rate,off_explosiveness')
    raw = _page('ncaaf_team_game_stats', {'select': sel})
    # home/away is not on this table; derive it from the results table
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
            continue                       # cannot place the game; skip
        if metric in ('points', 'margin'):
            p, q = _f(r.get('points')), _f(r.get('opp_points'))
            if p is None or q is None:
                continue
            # 2026-10-09 · USE POINTS, NOT MARGIN, AND THIS IS NOT A
            # PREFERENCE. Margin is antisymmetric: one team's +10 is the
            # other's -10. Writing both rows of a game as
            #     off_i - def_j = -(off_j - def_i)
            # forces off_i + off_j = def_i + def_j for EVERY pairing, which
            # collapses to off_t = def_t + c for all t. The first run showed
            # exactly that -- Indiana off +16.28 / def +16.28, identical for
            # every team -- so offence and defence were not separately
            # identifiable at all. Points scored are two independent
            # observations per game and identify both halves, which is why
            # SRS and SP+ model scoring rather than margin.
            perf = p - q if metric == 'margin' else p
        else:
            perf = _f(r.get(metric))
            if perf is None:
                continue
        out.append({'date': d, 'team': t, 'opp': o, 'is_home': is_home,
                    'season': int(r.get('season') or 0), 'perf': perf})
    out.sort(key=lambda z: z['date'])
    return out


def fit(games: list[dict], cur_season: int) -> tuple[dict, dict, float]:
    """Ridge-solve perf = off_i - def_j + home_adv. Returns (off, def, home)."""
    teams = sorted({g['team'] for g in games} | {g['opp'] for g in games})
    ix = {t: i for i, t in enumerate(teams)}
    n_t = len(teams)
    rows, ys, ws = [], [], []
    for g in games:
        v = np.zeros(2 * n_t + 2)
        v[ix[g['team']]] = 1.0                      # offence of the team
        v[n_t + ix[g['opp']]] = -1.0                # defence of the opponent
        v[-2] = g['is_home']
        v[-1] = 1.0    # intercept
        rows.append(v)
        ys.append(g['perf'])
        ws.append(SEASON_DECAY.get(cur_season - g['season'], 0.05))
    X = np.asarray(rows)
    y = np.asarray(ys)
    w = np.sqrt(np.asarray(ws))
    Xw = X * w[:, None]
    yw = y * w
    # do not penalise the home-advantage term
    pen = np.eye(X.shape[1]) * LAMBDA
    pen[-1, -1] = 0.0      # intercept unpenalised
    pen[-2, -2] = 0.0      # home advantage unpenalised
    beta = np.linalg.solve(Xw.T @ Xw + pen, Xw.T @ yw)
    off = {t: float(beta[ix[t]]) for t in teams}
    dfn = {t: float(beta[n_t + ix[t]]) for t in teams}
    return off, dfn, float(beta[-2])



def validate(games: list[dict], season: int, min_games: int) -> None:
    """Walk-forward: fit on games BEFORE a date, predict the games ON it.

    This is the only number that matters. A rating that ranks teams well but
    cannot beat the closing line is a nice table, not an edge — and this
    project has a standing finding that no side model beats the close
    (project_models_dont_beat_the_close_1005). Nothing from the predicted
    week informs the ratings used to predict it.

    Expected home margin falls straight out of the points model:
        E[pts_home] = off_h - def_a + home + c
        E[pts_away] = off_a - def_h + c
        E[margin]   = (off_h + def_h) - (off_a + def_a) + home
                    = rating_h - rating_a + home
    """
    res = {}
    for g in _page('ncaaf_game_results',
                   {'select': 'game_date,home_team,away_team,close_spread,'
                              'spread_result',
                    'game_date': f'gte.{season}-08-01'}):
        if str(g.get('spread_result') or '').lower() not in (
                'home_covered', 'away_covered'):
            continue
        cs = _f(g.get('close_spread'))
        if cs is None:
            continue
        res.setdefault(str(g['game_date'])[:10], []).append(g)

    rows = []
    for d in sorted(res):
        prior = [x for x in games if x['date'] < d]
        if len(prior) < 400:
            continue
        off, dfn, home = fit(prior, season)
        rating = {t: off.get(t, 0.0) + dfn.get(t, 0.0) for t in off}
        seen = collections.Counter(x['team'] for x in prior)
        for g in res[d]:
            h, aw = str(g['home_team']), str(g['away_team'])
            if seen[h] < min_games or seen[aw] < min_games:
                continue
            exp = rating.get(h, 0.0) - rating.get(aw, 0.0) + home
            cs = _f(g['close_spread'])
            # NCAAF convention (verified empirically 2026-10-09): negative
            # close_spread = home favourite, so the market's expected home
            # margin is -close_spread.
            mkt = -cs
            rows.append({'edge': exp - mkt,
                         'home_cov': str(g['spread_result']).lower()
                                     == 'home_covered'})
    print()
    print(f'  WALK-FORWARD COVER TEST — {len(rows)} games predicted from '
          f'prior games only')
    if len(rows) < 60:
        print('    too few to judge. Not drawing a conclusion.')
        return
    base = sum(r['home_cov'] for r in rows) / len(rows) * 100
    print(f'    base home-cover rate {base:.1f}%')
    for lo, hi, lbl in ((-999, -7, 'market likes home by 7+ more'),
                        (-7, -3, 'market likes home by 3-7 more'),
                        (-3, 3, 'we and the market agree (+/-3)'),
                        (3, 7, 'we like home by 3-7 more'),
                        (7, 999, 'we like home by 7+ more')):
        sub = [r for r in rows if lo <= r['edge'] < hi]
        if len(sub) < 15:
            print(f'    {lbl:<34}n={len(sub)} — too few')
            continue
        p = sum(r['home_cov'] for r in sub) / len(sub) * 100
        se = (0.5 / len(sub) ** 0.5) * 100
        print(f'    {lbl:<34}{p:5.1f}%  n={len(sub):<4} +/-{2 * se:4.1f}pp')
    # the actual betting rule: back whichever side our edge favours
    wins = sum(1 for r in rows
               if (r['edge'] > 0) == r['home_cov'])
    n = len(rows)
    p = wins / n * 100
    se = (0.5 / n ** 0.5) * 100
    print()
    print(f'    BACK OUR SIDE EVERY GAME: {wins}-{n - wins}  {p:.1f}%  '
          f'+/-{2 * se:.1f}pp (2SE) · breakeven 52.38%')
    print(f'    {"BEATS" if p - 2 * se > 52.38 else "does NOT beat"} '
          f'breakeven at two standard errors')
    big = [r for r in rows if abs(r['edge']) >= 7]
    if len(big) >= 30:
        w2 = sum(1 for r in big if (r['edge'] > 0) == r['home_cov'])
        p2 = w2 / len(big) * 100
        s2 = (0.5 / len(big) ** 0.5) * 100
        print(f'    only when |edge| >= 7:   {w2}-{len(big) - w2}  {p2:.1f}%  '
              f'+/-{2 * s2:.1f}pp  n={len(big)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--metric', default='points',
                    choices=('points', 'margin', 'off_ppa',
                             'off_success_rate', 'off_explosiveness'),
                    help="'margin' is kept for comparison but CANNOT "
                         "separate offence from defence -- see load_games()")
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--top', type=int, default=10)
    ap.add_argument('--min-games', type=int, default=3,
                    dest='min_games')
    ap.add_argument('--validate', action='store_true',
                    help='walk-forward cover test (the real bar)')
    a = ap.parse_args()

    games = load_games(a.metric)
    if not games:
        raise SystemExit('no team-games loaded')
    print(f'=== opponent-adjusted rating · metric={a.metric} · '
          f'{len(games)} team-games {games[0]["date"]}..{games[-1]["date"]}')
    by_s = collections.Counter(g['season'] for g in games)
    print(f'    by season: {dict(sorted(by_s.items()))} · '
          f'ridge lambda={LAMBDA} · season weights {SEASON_DECAY}')

    off, dfn, home = fit(games, a.season)
    # 2026-10-09 SIGN FIX. The model is perf_i = off_i - def_j, so def_j is
    # how much team j's defence SUPPRESSES its opponent — higher is better.
    # Quality is therefore off + def, not off - def. The first run used the
    # difference and produced a table with Tennessee State and Stonehill on
    # top and Texas Tech last, which is how the error announced itself.
    overall = {t: off[t] + dfn[t] for t in off}
    _gc = collections.Counter(g['team'] for g in games
                              if g['season'] == a.season)
    # Same discriminator compute_schedule_strength settled on: an FBS team
    # plays a full schedule, an FCS team appears only via one or two
    # crossover games, so a games-played floor separates them without an
    # alias table.
    cur_teams = {t for t, c in _gc.items() if c >= a.min_games}
    ranked = sorted([(v, t) for t, v in overall.items() if t in cur_teams],
                    reverse=True)
    print(f'\n    home advantage: {home:+.2f} ({a.metric} units)')
    print(f'    {len(ranked)} teams rated for {a.season}')
    print(f'\n    TOP {a.top}:')
    for v, t in ranked[:a.top]:
        print(f'      {t:<26}{v:+7.2f}   off {off[t]:+6.2f}  def {dfn[t]:+6.2f}')
    print(f'    BOTTOM 5:')
    for v, t in ranked[-5:]:
        print(f'      {t:<26}{v:+7.2f}   off {off[t]:+6.2f}  def {dfn[t]:+6.2f}')

    # ── the new SOS/SOR, in Andy's sense ─────────────────────────────────
    faced = collections.defaultdict(list)
    for g in games:
        if g['season'] == a.season and g['team'] in cur_teams:
            faced[g['team']].append(overall.get(g['opp'], 0.0))
    sos = {t: statistics.fmean(v) for t, v in faced.items() if v}
    print(f'\n    SOS = mean RATING of opponents faced (not their record).')
    hard = sorted(((v, t) for t, v in sos.items()), reverse=True)[:5]
    easy = sorted(((v, t) for t, v in sos.items()))[:5]
    print('      hardest schedules:', ', '.join(f'{t} {v:+.1f}' for v, t in hard))
    print('      easiest schedules:', ', '.join(f'{t} {v:+.1f}' for v, t in easy))
    print('\n    SOR in this framework is just the team\'s own rating — it is')
    print('    ALREADY schedule-adjusted, which is the whole point. A win over')
    print('    a team that later rates well raises your rating retroactively.')

    if a.validate:
        validate(games, a.season, a.min_games)
    return 0


if __name__ == '__main__':
    sys.exit(main())
