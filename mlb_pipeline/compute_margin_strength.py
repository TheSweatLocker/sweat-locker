#!/usr/bin/env python3
"""Margin-based strength of schedule and strength of record, every sport.

WHY THIS EXISTS ALONGSIDE compute_schedule_strength.py
------------------------------------------------------
That script computes SOS and SOR from WIN PERCENTAGE only, shrunk toward .500
with head-to-head removed. It is honest about what it is, but it throws away
the most informative thing on the scoreboard: a one-point win and a thirty-
point win count identically. At three or four games played — which is where
NFL sits in week 4, and where a rating actually gets used — opponent win% is
close to pure noise.

Andy 2026-10-04: "SOR/SOS is very important ... I want improvement now."

THE MODEL is the Simple Rating System, solved the same way as
nfl_opponent_adjusted_epa:

    rating[t] = average( adjusted_margin[t, g] + rating[opponent] )

iterated to convergence with ridge shrinkage. A team's rating is its scoring
margin corrected for who it played, so it answers the question SOR is actually
asking — would an average team have done this against this schedule?

    SOS = average rating of the opponents faced   (in POINTS, not win share)
    SOR = the team's own rating

THREE CORRECTIONS THAT MATTER MORE THAN THE SOLVER
--------------------------------------------------
* HOME FIELD. A road win is worth more than a home win. Margin is adjusted by
  a per-sport HFA before anything else, so beating a team at their place
  outrates beating them at yours.
* BLOWOUT DAMPING. A 45-point win is not three times more informative than a
  15-point win — it mostly measures garbage time and when the starters sat.
  Margins are capped per sport. Without this, one blowout can carry a rating
  for a month.
* SHRINKAGE SCALED TO SCHEDULE LENGTH. K=4 games is right for a 17-game NFL
  season and badly wrong for a 162-game MLB one. Each sport gets its own.

VALIDATED, not assumed — see --validate. It walks forward, rating only on
games strictly before the week being predicted, and compares against both raw
margin and the existing win%-based measure.

    python compute_margin_strength.py --validate --sport NFL
    python compute_margin_strength.py --sport NFL --season 2026 --write
"""
from __future__ import annotations
import argparse, collections, os, sys
from datetime import date
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

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

RESULTS = {
    'MLB': 'mlb_game_results', 'NFL': 'nfl_game_results',
    'NCAAF': 'ncaaf_game_results', 'NHL': 'nhl_game_results',
    'NBA': 'nba_game_results', 'NCAAB': 'ncaab_game_results',
}

# (home-field points, margin cap, ridge in games-equivalent).
# HFA and cap are scale decisions about each sport's scoreboard, not fits:
# an NHL game is decided by two goals, an NCAAF game sometimes by forty.
SPORT_CFG = {
    'NFL':   {'hfa': 2.0, 'cap': 24.0, 'ridge': 4.0},
    'NCAAF': {'hfa': 2.5, 'cap': 28.0, 'ridge': 6.0},
    'NBA':   {'hfa': 2.5, 'cap': 20.0, 'ridge': 8.0},
    'NCAAB': {'hfa': 3.0, 'cap': 20.0, 'ridge': 8.0},
    'NHL':   {'hfa': 0.2, 'cap': 3.0,  'ridge': 10.0},
    'MLB':   {'hfa': 0.2, 'cap': 6.0,  'ridge': 20.0},
}
ITERS = 30


def _pull(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        q = dict(params)
        q.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q, timeout=180)
        r.raise_for_status()
        chunk = r.json()
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_games(sport: str, season=None) -> list[dict]:
    tbl = RESULTS[sport]
    params = {'select': '*'}
    if season is not None:
        params['season'] = f'eq.{season}'
    rows = _pull(tbl, params)
    out = []
    for g in rows:
        hs, as_ = _f(g.get('home_score')), _f(g.get('away_score'))
        h, a = g.get('home_team'), g.get('away_team')
        if hs is None or as_ is None or not h or not a:
            continue
        out.append({'home': h, 'away': a, 'margin': hs - as_,
                    'date': g.get('game_date'), 'season': g.get('season'),
                    'neutral': bool(g.get('neutral_site'))})
    return out


def fit_srs(games, cfg, iters: int = ITERS) -> dict:
    """-> {team: rating}, in points, opponent-adjusted."""
    if not games:
        return {}
    hfa, cap, ridge = cfg['hfa'], cfg['cap'], cfg['ridge']
    obs = collections.defaultdict(list)
    for g in games:
        m = g['margin'] - (0.0 if g.get('neutral') else hfa)
        m = max(-cap, min(cap, m))
        obs[g['home']].append((g['away'], m))
        obs[g['away']].append((g['home'], -m))
    rating = collections.defaultdict(float)
    for _ in range(iters):
        nxt = {}
        for t, lst in obs.items():
            nxt[t] = sum(m + rating[o] for o, m in lst) / (len(lst) + ridge)
        # Re-centre so ratings are relative to an average team, which is what
        # makes "average opponent rating" readable as schedule strength.
        mu = sum(nxt.values()) / len(nxt) if nxt else 0.0
        rating = collections.defaultdict(float, {t: v - mu for t, v in nxt.items()})
    return dict(rating)


def sos_from(games, rating) -> dict:
    """Average rating of the opponents each team actually played."""
    opp = collections.defaultdict(list)
    for g in games:
        opp[g['home']].append(rating.get(g['away'], 0.0))
        opp[g['away']].append(rating.get(g['home'], 0.0))
    return {t: sum(v) / len(v) for t, v in opp.items() if v}


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    return sxy / ((sxx * syy) ** 0.5) if sxx and syy else 0.0


def validate(sport: str, cfg) -> int:
    games = load_games(sport)
    games = [g for g in games if g.get('date')]
    games.sort(key=lambda g: g['date'])
    print(f'{sport}: {len(games)} graded games\n')

    by_season = collections.defaultdict(list)
    for g in games:
        by_season[g.get('season') or str(g['date'])[:4]].append(g)

    srs_p, winp_p, rawm_p, act = [], [], [], []
    for season, gs in sorted(by_season.items(), key=lambda kv: str(kv[0])):
        gs.sort(key=lambda g: g['date'])
        n = len(gs)
        if n < 60:
            continue
        # Walk forward in thirds: rate on the past, predict the next slice.
        for cut in (int(n * 0.4), int(n * 0.6), int(n * 0.8)):
            hist, fut = gs[:cut], gs[cut:int(cut + n * 0.2)]
            if len(hist) < 40 or not fut:
                continue
            rating = fit_srs(hist, cfg)
            # win%-based control, the measure we ship today
            w = collections.defaultdict(lambda: [0, 0])
            tot = collections.defaultdict(list)
            for g in hist:
                hw = g['margin'] > 0
                w[g['home']][0 if hw else 1] += 1
                w[g['away']][1 if hw else 0] += 1
                tot[g['home']].append(g['margin'])
                tot[g['away']].append(-g['margin'])
            winpct = {t: v[0] / max(1, v[0] + v[1]) for t, v in w.items()}
            rawm = {t: sum(v) / len(v) for t, v in tot.items()}
            for g in fut:
                h, a = g['home'], g['away']
                if h not in rating or a not in rating:
                    continue
                srs_p.append(rating[h] - rating[a])
                winp_p.append(winpct.get(h, .5) - winpct.get(a, .5))
                rawm_p.append(rawm.get(h, 0) - rawm.get(a, 0))
                act.append(g['margin'])
    print('=== out-of-sample, walk-forward (rate on earlier games only) ===')
    print(f'  n = {len(act)} games')
    print(f'  win% differential  (what we ship today) corr = {_corr(winp_p, act):+.3f}')
    print(f'  raw margin differential                 corr = {_corr(rawm_p, act):+.3f}')
    print(f'  SRS opponent-adjusted margin            corr = {_corr(srs_p, act):+.3f}')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(RESULTS))
    ap.add_argument('--season', type=int)
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    cfg = SPORT_CFG[args.sport]

    if args.validate:
        return validate(args.sport, cfg)

    season = args.season or date.today().year
    games = load_games(args.sport, season)
    if not games:
        print(f'no {args.sport} games for {season}')
        return 0
    rating = fit_srs(games, cfg)
    sos = sos_from(games, rating)
    print(f'=== {args.sport} {season} · margin SOS/SOR '
          f'(hfa={cfg["hfa"]} cap={cfg["cap"]} ridge={cfg["ridge"]}) ===')
    print('%-6s %9s %9s' % ('team', 'SOR', 'SOS'))
    for t in sorted(rating, key=lambda x: -rating[x]):
        print('%-6s %9.2f %9.2f' % (t, rating[t], sos.get(t, 0.0)))

    if args.write:
        payload = []
        for key, vals, label in (('sor_margin', rating, 'Strength of Record (margin)'),
                                 ('sos_margin', sos, 'Strength of Schedule (margin)')):
            order = sorted(vals, key=lambda x: -vals[x])
            for i, t in enumerate(order, 1):
                payload.append({'sport': args.sport, 'team': t, 'season': season,
                                'stat_key': key, 'raw_value': round(vals[t], 3),
                                'rank': i, 'league_size': len(order),
                                'direction': 'higher', 'display_label': label,
                                'unit': ''})
        r = requests.post(f'{SB}/rest/v1/team_computed_stats'
                          '?on_conflict=sport,team,season,stat_key',
                          headers=H_W, json=payload, timeout=90)
        print(f'\nwrite {len(payload)} rows -> {r.status_code} {r.text[:140]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
