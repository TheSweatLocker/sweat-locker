#!/usr/bin/env python3
"""The Sweat Rating — one proprietary 0-100 number per team, every sport.

Andy's spec (2026-10-03): "a sweat rating for a team almost like a Madden
rating when you see it, a proprietary rating of a team's ability to perform and
win games, using live rolling stats, how they perform week to week, how they
perform in certain situations — do they beat teams by a lot when they are
supposed to, do they beat capable teams."

WHY IT CAN EXIST WHEN A SPREAD MODEL CANNOT
-------------------------------------------
A rating is a DESCRIPTOR. It has to be true, not profitable. Our spread model
fails because it has to beat a closing line set by the whole market; a rating
only has to describe what has happened better than the alternatives, and
compute_margin_strength already shows it does — opponent-adjusted margin beats
win% on all six sports out of sample. So this ships now while a spread model
does not, and that difference is the whole reason it is worth building.

THE FOUR COMPONENTS, and what each is there to stop
---------------------------------------------------
1. QUALITY (55%) — opponent-adjusted margin (SOR). The backbone. Alone it
   over-credits a team that beat up a weak schedule by exactly its own margin.
2. SCHEDULE (15%) — the strength of who they played (SOS). This is the "do
   they beat capable teams" half of the spec. Without it a 5-0 against nobody
   outranks 4-1 against the field.
3. CONSISTENCY (15%) — the spread of their game-by-game adjusted margins,
   inverted. A team that wins by 3 every week is a better bet than one
   alternating +28 and -21 to the same average, and a season average hides
   exactly that.
4. AUTHORITY (15%) — margin relative to what the MARKET expected, i.e. cover
   margin against the closing line. Do they take care of business when they
   are installed as the favourite. Measured against the market and not against
   our own rating, for the reason in the next section.

KNOWN OVERLAP, stated rather than hidden. Measured on NFL 2026 at 3 games:

    quality <-> authority      +0.849
    quality <-> schedule       -0.454
    schedule <-> authority     -0.517
    quality <-> consistency    -0.042
    consistency <-> authority  -0.022

Quality and authority share the margin term by construction — cover margin IS
margin plus the line — so at three games, where the line contributes little
independent variance, they move together. The first version was far worse
(effectively 1.00, because authority was measured against our own rating), and
this is the fix. But it means the stated 55/15 split currently behaves closer
to a single 70-weight margin lens than two. RE-CHECK THIS at 8+ games before
treating the weights as literal; if it has not separated by then, authority
should be cut to a cover RATE, which shares no term with margin at all.

SHRINKAGE IS NOT OPTIONAL. Early season every component is noise, so the whole
rating is pulled toward the league middle by games played. An NFL team in week
2 should read ~75 and boring, not 97 — a rating that swings wildly in
September teaches people to ignore it in November.

SCALE. Centred on 75 with 8 points per standard deviation, clamped [40, 99] —
deliberately Madden-shaped, because that is the shape the spec asked for and
the one people already read without a key.

    python compute_sweat_rating.py --sport NFL
    python compute_sweat_rating.py --sport NFL --write
    python compute_sweat_rating.py --all --write
"""
from __future__ import annotations
import argparse, collections, os, statistics, sys
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

from compute_margin_strength import (RESULTS, SPORT_CFG, load_games, fit_srs,
                                     load_with_carryover, CARRYOVER_FULL,
                                     sos_from, publishable_teams)

WEIGHTS = {'quality': 0.55, 'schedule': 0.15,
           'consistency': 0.15, 'authority': 0.15}
CENTRE, PER_SD = 75.0, 8.0
FLOOR, CEIL = 40.0, 99.0
# Games at which the rating is trusted at full strength, per sport. A rating
# that is confident in week 2 is a rating nobody believes in week 12.
FULL_TRUST = {'NFL': 8, 'NCAAF': 7, 'NBA': 25, 'NCAAB': 15,
              'NHL': 25, 'MLB': 50}


def _z(vals: dict) -> dict:
    """Standardise within the sport; a flat field yields all zeroes."""
    if len(vals) < 2:
        return {k: 0.0 for k in vals}
    xs = list(vals.values())
    mu = statistics.mean(xs)
    sd = statistics.pstdev(xs)
    if sd <= 1e-9:
        return {k: 0.0 for k in vals}
    return {k: (v - mu) / sd for k, v in vals.items()}


# How much a PRIOR-season game counts toward trust, relative to a current one.
# 2026-10-04: NBA's regular season does not open until 10-21 (sport_registry),
# so the current season has ZERO real games and the rating is entirely last
# season's. Driving shrinkage off current-season games alone would print 75.0
# for all 30 teams — the dead-flat state NHL is in tonight. But 82 games of
# last season IS evidence; it is just stale, because rosters turn over. Half
# weight is a judgement call, not a measurement, and it is FLAGGED FOR ANDY:
# it decides how confident an opening-night rating looks.
PRIOR_TRUST_DISCOUNT = 0.5


def components(sport: str, season: int):
    cfg = SPORT_CFG[sport]
    # load_with_carryover drops exhibitions/preseason and blends the prior
    # season at a decaying weight for the sports configured for it; for every
    # other sport it returns exactly what load_games returned.
    games = load_with_carryover(sport, season, quiet=True)
    if not games:
        return None
    rating = fit_srs(games, cfg)
    sos = sos_from(games, rating)

    # Per-game adjusted margin, from each team's own point of view.
    hfa, cap = cfg['hfa'], cfg['cap']
    per_game = collections.defaultdict(list)
    expected = collections.defaultdict(list)
    for g in games:
        m = g['margin'] - (0.0 if g.get('neutral') else hfa)
        m = max(-cap, min(cap, m))
        h, a = g['home'], g['away']
        per_game[h].append(m)
        per_game[a].append(-m)
        # ══ AUTHORITY MUST NOT BE QUALITY WEARING A HAT ══
        # First version measured margin against OUR OWN rating differential.
        # That rating is fit from these same margins, so the two came back
        # effectively identical — SF 1.91 quality vs 1.90 authority, JAX
        # 1.86/1.86, CHI 1.77/1.77. Four weights, three real signals.
        #
        # "Supposed to" means what the MARKET expected, not what we expected.
        # Cover margin against the closing line is a genuinely separate input,
        # and it is the thing the spec was describing: do they take care of
        # business when they are installed as the favourite.
        hl = g.get('home_line')
        if hl is None:
            continue
        cover = m + hl          # >0 means the home side beat the number
        expected[h].append(cover)
        expected[a].append(-cover)

    # Trust-weighted game count: a carried-over game counts for less, so an
    # opening-night rating is confident-but-not-certain rather than flat.
    gp = collections.defaultdict(float)
    for g in games:
        w = float(g.get('weight', 1.0) or 1.0)
        eff = PRIOR_TRUST_DISCOUNT * w if g.get('prior_season') else w
        gp[g['home']] += eff
        gp[g['away']] += eff
    gp = {t: gp.get(t, 0.0) for t in per_game}
    consistency = {}
    for t, v in per_game.items():
        # Low spread = dependable. Single-game teams get the league's worst
        # spread rather than a flattering zero.
        consistency[t] = -statistics.pstdev(v) if len(v) > 1 else None
    worst = min([c for c in consistency.values() if c is not None], default=0.0)
    consistency = {t: (c if c is not None else worst) for t, c in consistency.items()}
    authority = {t: statistics.mean(v) for t, v in expected.items() if v}
    return {'rating': rating, 'sos': sos, 'consistency': consistency,
            'authority': authority, 'gp': gp, 'games': games}


def sweat_rating(sport: str, season: int):
    c = components(sport, season)
    if not c:
        return {}, {}
    zq = _z(c['rating'])
    zs = _z(c['sos'])
    zc = _z(c['consistency'])
    za = _z(c['authority'])
    full = FULL_TRUST.get(sport, 10)
    out, detail = {}, {}
    for t in c['rating']:
        blended = (WEIGHTS['quality'] * zq.get(t, 0.0)
                   + WEIGHTS['schedule'] * zs.get(t, 0.0)
                   + WEIGHTS['consistency'] * zc.get(t, 0.0)
                   + WEIGHTS['authority'] * za.get(t, 0.0))
        n = c['gp'].get(t, 0)
        trust = min(1.0, n / float(full)) if full else 1.0
        val = CENTRE + PER_SD * blended * trust
        out[t] = round(max(FLOOR, min(CEIL, val)), 1)
        detail[t] = {'gp': round(n, 1), 'trust': round(trust, 2),
                     'quality': round(zq.get(t, 0.0), 2),
                     'schedule': round(zs.get(t, 0.0), 2),
                     'consistency': round(zc.get(t, 0.0), 2),
                     'authority': round(za.get(t, 0.0), 2)}
    return out, detail


def run(sport: str, season: int, write: bool) -> None:
    ratings, detail = sweat_rating(sport, season)
    if not ratings:
        print(f'{sport}: no games for {season}')
        return
    print(f'\n=== SWEAT RATING · {sport} {season} '
          f'(trust at {FULL_TRUST.get(sport)} games) ===')
    print('%-26s %6s %4s %6s %7s %7s %7s %7s'
          % ('team', 'SWEAT', 'gp', 'trust', 'qual', 'sched', 'consis', 'auth'))
    for t in sorted(ratings, key=lambda x: -ratings[x])[:32]:
        d = detail[t]
        print('%-26s %6.1f %4d %6.2f %7.2f %7.2f %7.2f %7.2f'
              % (t[:26], ratings[t], d['gp'], d['trust'], d['quality'],
                 d['schedule'], d['consistency'], d['authority']))
    if write:
        # FBS only for NCAAF — the fit used every game, but a 261-team table
        # ranks Mercyhurst against Alabama and the rank stops meaning anything.
        keep = publishable_teams(sport, season)
        if keep is not None:
            before = len(ratings)
            ratings = {t: v for t, v in ratings.items() if t in keep}
            print(f'  publishing {len(ratings)} of {before} teams (FBS only)')
        order = sorted(ratings, key=lambda x: -ratings[x])
        payload = [{'sport': sport, 'team': t, 'season': season,
                    'stat_key': 'sweat_rating', 'raw_value': ratings[t],
                    'rank': i, 'league_size': len(order),
                    'direction': 'higher', 'display_label': 'Sweat Rating',
                    'unit': ''}
                   for i, t in enumerate(order, 1)]
        r = requests.post(f'{SB}/rest/v1/team_computed_stats'
                          '?on_conflict=sport,team,season,stat_key',
                          headers=H_W, json=payload, timeout=90)
        print(f'\nwrite {len(payload)} rows -> {r.status_code} {r.text[:140]}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(RESULTS))
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--season', type=int, default=date.today().year)
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    sports = sorted(RESULTS) if args.all else [args.sport]
    if not sports or sports == [None]:
        ap.error('pass --sport or --all')
    for s in sports:
        try:
            run(s, args.season, args.write)
        except Exception as e:
            print(f'{s}: skipped — {type(e).__name__}: {e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
