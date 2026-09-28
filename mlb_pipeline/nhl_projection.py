"""NHL goal projection + Monte Carlo, built on the 32-team rolling ratings.

Andy 2026-09-27: "NHL will be nearly daily games to support non-football
days ... I would love score predictions based on rolling live stats ...
we should have 5 models at least for each sport, each having a take."

This supplies two of those five. Before it, NHL ran LR + ensemble + Elo
and had mc_probabilities populated on 0 of 59 games, against NFL's six
lenses.

── THE PROJECTION ────────────────────────────────────────────────────────
Multiplicative matchup, the standard way to combine an attack rate with a
defensive one:

    team_rate = (own_attack / league_attack) * (opp_defence / league_defence)
                * league_attack

so a team 10% above average attacking a defence 10% worse than average
projects 21% above average, not 20%. Additive blending understates
mismatches, which on a two-and-a-half goal scale is most of the signal.

EXPECTED goals lead ACTUAL goals. xGF/60 is a shot-quality measure and
stabilises in roughly a quarter of the sample that GF/60 needs, so it
carries 65% of the attack term. Actual finishing is real talent though —
some rosters genuinely convert above expectation — so GF/60 keeps 35%
rather than being discarded.

GOALTENDING IS SEPARATE FROM TEAM DEFENCE. Shot suppression and shot
stopping are different skills with different persistence, and treating
them as one number is why "good defensive team" reads can be so wrong.
5v5 save percentage adjusts the opponent's expectation directly.

SCALING TO A FULL GAME. Every input is a per-60-minutes-of-5v5 rate, and
a game holds only ~48 minutes of 5v5, with the balance on special teams
and 6-on-5. Rather than hard-code that split — it drifts year to year —
the module CALIBRATES: it computes the league-average matchup from the
same ratings and scales so the mean team lands on NHL_AVG_GOALS, the
real league scoring rate. If the source rates change scale, the
calibration absorbs it instead of the projection quietly drifting.

── THE MONTE CARLO ───────────────────────────────────────────────────────
Goals are close to Poisson, so the two projected rates are simulated
directly rather than assumed normal. That matters for a 5.5 total, where
the distribution is visibly skewed and a normal approximation misprices
the tail.

REGULATION TIES ARE NOT COIN FLIPS AND ARE NOT SKILL EITHER. ~23% of NHL
games reach OT, and 3-on-3 plus a shootout is far closer to even than the
teams' underlying strength. The sim resolves ties at a strength-weighted
probability pulled most of the way back toward 50/50 — ignoring this
overstates a favourite's moneyline by several points, which is exactly
the error that makes a model look like it has edge on chalk when it does
not.
"""
from __future__ import annotations

import math
import os
import random
from typing import Optional

import requests

# Real NHL scoring, goals per team per game, all situations. The single
# calibration anchor for the whole module.
NHL_AVG_GOALS = 3.05

# Home ice, applied SYMMETRICALLY (+0.15 home, -0.15 away) so it moves the
# MARGIN without inventing scoring. An earlier +0.20/-0.10 split pushed the
# mean projected total to 6.20 against a 6.10 league rate — home ice was
# adding a tenth of a goal to every game in the league.
#
# 0.15 is calibrated to the HOME WIN RATE, not to a goal differential.
# Simulating two identical teams:
#       0.10 -> p(home) 0.531
#       0.15 -> p(home) 0.546     <- real NHL is ~0.545 including OT
#       0.20 -> p(home) 0.564
# The win rate is the right anchor because it is the number the moneyline
# pick actually depends on, and it is a hard, well-established figure.
# It does leave the projected margin at +0.30 against a real ~+0.25, which
# is the price of matching the thing that matters.
HOME_ICE_GOALS = 0.15

# ══ NHL TIES ARE MORE COMMON THAN POISSON CAN PRODUCE ══
#
# Independent Poisson at the league scoring rate gives P(regulation tie) =
# 0.165. The real NHL rate is 0.230. That 6.5-point gap is not noise and
# no amount of re-tuning the goal rates closes it, because the cause is
# not in the rates: a trailing team pulls its goalie and presses while a
# leading team defends, so one-goal games collapse into ties far more
# often than two independent processes would allow.
#
# Modelled directly, the way it actually happens: when regulation ends one
# goal apart, the trailing side equalises with probability TIE_PULL. 0.22
# is derived, not guessed — P(margin = 1) at these rates is ~0.29, and
# 0.165 + 0.29 * 0.22 lands on 0.230.
#
# Getting this wrong matters for the pick, not just the OT chip: every tie
# the sim fails to produce is handed to the stronger team as a regulation
# win, which inflates favourites' moneyline probability. That is exactly
# the error that makes a model look like it has edge on chalk when it has
# none.
TIE_PULL = 0.22

# Attack term weights. xG leads because it stabilises faster; actual goals
# stay in because finishing talent is real.
W_XG, W_ACTUAL = 0.65, 0.35

# ══ SHRINKAGE — THE INPUTS ARE 5v5, THE PREDICTION IS A WHOLE GAME ══
#
# Without this the model was visibly wrong against three things the NHL
# fixes for us. Projecting Vancouver @ Edmonton it returned:
#
#       Edmonton 4.19 goals      league average is 3.05, and the WORST
#                                real defence allows about 3.5
#       OT rate 13-17%           the real figure is ~23%
#       p(home) 0.70             NHL home teams win ~55% overall
#
# All three are the same fault: the relative spread in 5v5 shot-quality
# rates is WIDER than the spread in full-game goals. Special teams,
# score effects and goalie variance all compress the gap between good and
# bad teams over a full game, so a ratio taken at 5v5 and applied whole
# over-amplifies every mismatch.
#
# The deviation from average is therefore shrunk toward 1.0 before
# scaling. 0.62 is not a guess — it is the value that puts the simulated
# OT rate on the real ~23%, and OT rate is the cleanest available check on
# the margin distribution: it is determined entirely by how often the sim
# produces a tie, which is exactly what "are these margins right" means.
# The league mean is untouched by shrinkage (a mean deviation of 1.0
# shrinks to 1.0), so the calibration to NHL_AVG_GOALS still holds.
SPREAD_SHRINK = 0.62

# How far an OT/shootout result is pulled back toward a coin flip. 0.80
# means a team the sim rates 60% to win in regulation is only ~52% to win
# a 3-on-3 plus shootout.
OT_REGRESSION = 0.80
OT_SHARE = 0.23          # share of games reaching OT, for reference/logging

_LEAGUE_CACHE: dict = {}


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def load_team_ratings(sb: str, headers: dict, season: int) -> dict:
    """-> {team_name: {stat_key: raw_value}} for all NHL teams."""
    key = ('NHL', season)
    if key in _LEAGUE_CACHE:
        return _LEAGUE_CACHE[key]
    out: dict = {}
    try:
        r = requests.get(f'{sb}/rest/v1/team_stats_rolling', headers=headers,
                         params={'select': 'team,stat_key,raw_value',
                                 'sport': 'eq.NHL',
                                 'season': f'eq.{season}',
                                 'limit': '2000'}, timeout=25)
        if r.status_code != 200:
            return {}
        rows = r.json()
        if not isinstance(rows, list):
            return {}
        for x in rows:
            v = _f(x.get('raw_value'))
            if v is not None:
                out.setdefault(x['team'], {})[x['stat_key']] = v
    except Exception:
        return {}
    _LEAGUE_CACHE[key] = out
    return out


def _league_means(ratings: dict) -> dict:
    """League-average attack, defence and goaltending across all teams."""
    def mean(k):
        vals = [s[k] for s in ratings.values() if s.get(k) is not None]
        return sum(vals) / len(vals) if vals else None
    return {
        'xgf': mean('xgf_per60'), 'xga': mean('xga_per60'),
        'gf': mean('gf_per60'),   'ga': mean('ga_per60'),
        'sv': mean('save_pct_5v5'),
    }


def _raw_rate(off: dict, deff: dict, lg: dict) -> Optional[float]:
    """Unscaled expected goals for `off` attacking `deff`."""
    if not off or not deff or not lg:
        return None
    if not all(lg.get(k) for k in ('xgf', 'xga', 'gf', 'ga')):
        return None

    o_xg, o_g = off.get('xgf_per60'), off.get('gf_per60')
    d_xg, d_g = deff.get('xga_per60'), deff.get('ga_per60')
    if o_xg is None or d_xg is None:
        return None
    # Fall back to the xG side rather than dropping the team when the
    # actual-goals half is missing — a partial rating beats none.
    if o_g is None:
        o_g = o_xg
    if d_g is None:
        d_g = d_xg

    attack = W_XG * (o_xg / lg['xgf']) + W_ACTUAL * (o_g / lg['gf'])
    defend = W_XG * (d_xg / lg['xga']) + W_ACTUAL * (d_g / lg['ga'])
    # Shrink each side's deviation from average before combining — see the
    # SPREAD_SHRINK note. Applied per-term rather than to the product so
    # a team that is strong on both sides is not double-compressed.
    attack = 1.0 + (attack - 1.0) * SPREAD_SHRINK
    defend = 1.0 + (defend - 1.0) * SPREAD_SHRINK
    rate = attack * defend

    # Goaltending, applied to the SHOOTING side's expectation: a keeper
    # stopping more than average suppresses what the opponent scores.
    sv, lg_sv = deff.get('save_pct_5v5'), lg.get('sv')
    if sv is not None and lg_sv:
        # Each point of save percentage above average is worth a few
        # percent of goals against; the ratio of goals ALLOWED is
        # (1 - sv) / (1 - lg_sv).
        allowed = (100.0 - sv) / max(100.0 - lg_sv, 0.1)
        rate *= (0.5 + 0.5 * allowed)      # half weight — one of several inputs
    return rate


def project_goals(home_team: str, away_team: str, ratings: dict,
                  neutral: bool = False) -> Optional[dict]:
    """-> {home_goals, away_goals, total, margin} or None if unrated."""
    h, a = ratings.get(home_team), ratings.get(away_team)
    if not h or not a:
        return None
    lg = _league_means(ratings)
    h_raw = _raw_rate(h, a, lg)
    a_raw = _raw_rate(a, h, lg)
    if h_raw is None or a_raw is None:
        return None

    # Calibration: the league-average matchup must land on NHL_AVG_GOALS.
    # Rates are per 60 minutes of 5v5 while a game holds ~48, with the rest
    # on special teams — this absorbs that without hard-coding the split.
    ref = [_raw_rate(s, t, lg)
           for s in ratings.values() for t in ratings.values() if s is not t]
    ref = [x for x in ref if x is not None]
    scale = (NHL_AVG_GOALS / (sum(ref) / len(ref))) if ref else 1.0

    hg = h_raw * scale + (0.0 if neutral else HOME_ICE_GOALS)
    ag = a_raw * scale - (0.0 if neutral else HOME_ICE_GOALS)
    hg, ag = max(hg, 0.5), max(ag, 0.5)
    return {'home_goals': round(hg, 2), 'away_goals': round(ag, 2),
            'total': round(hg + ag, 2), 'margin': round(hg - ag, 2)}


def _pois(lmbda: float, rng: random.Random) -> int:
    """Knuth Poisson sampler — fine at NHL lambdas (~3)."""
    L, k, p = math.exp(-lmbda), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1
        if k > 30:
            return k


def monte_carlo(home_goals: float, away_goals: float, close_total=None,
                n: int = 10000, seed: int = 20260927) -> dict:
    """Simulate the game n times from the two projected goal rates."""
    rng = random.Random(seed)          # seeded: same inputs, same card
    hw = ot = 0
    tot_sum = mar_sum = 0.0
    overs = pushes = 0
    ct = _f(close_total)
    for _ in range(n):
        hg, ag = _pois(home_goals, rng), _pois(away_goals, rng)
        # Score effects: a one-goal game collapses into a tie when the
        # trailing side pulls its goalie and equalises. See TIE_PULL.
        if abs(hg - ag) == 1 and rng.random() < TIE_PULL:
            if hg > ag:
                ag += 1
            else:
                hg += 1
        tot_sum += hg + ag
        mar_sum += hg - ag
        if hg > ag:
            hw += 1
        elif hg == ag:
            ot += 1
            # Regulation tie -> OT/shootout, pulled toward even.
            edge = home_goals / max(home_goals + away_goals, 0.01)
            p_ot = 0.5 + (edge - 0.5) * (1.0 - OT_REGRESSION)
            if rng.random() < p_ot:
                hw += 1
        if ct is not None:
            s = hg + ag
            if s > ct:
                overs += 1
            elif s == ct:
                pushes += 1
    out = {
        'mc_p_home': round(hw / n, 4),
        'mc_expected_total': round(tot_sum / n, 2),
        'mc_expected_margin': round(mar_sum / n, 2),
        'mc_ot_rate': round(ot / n, 3),
        'mc_sims': n,
    }
    if ct is not None:
        decided = n - pushes
        out['mc_p_over'] = round(overs / decided, 4) if decided else None
    # A decisive read, used the same way the NFL MC chip uses it. The bar
    # is deliberately high: at a ~3-goal lambda most NHL games are close
    # to coin flips and a model claiming otherwise is usually wrong.
    out['mc_confidence_high'] = bool(
        out['mc_p_home'] >= 0.62 or out['mc_p_home'] <= 0.38)
    return out


def project_and_simulate(home_team: str, away_team: str, ratings: dict,
                         close_total=None, neutral: bool = False,
                         n: int = 10000) -> Optional[dict]:
    """Convenience: projection + MC in the shape nhl_game_context writes."""
    proj = project_goals(home_team, away_team, ratings, neutral=neutral)
    if not proj:
        return None
    mc = monte_carlo(proj['home_goals'], proj['away_goals'],
                     close_total=close_total, n=n)
    return {'projection': proj, 'mc': mc}


if __name__ == '__main__':
    import sys
    from pathlib import Path
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    _e = Path(__file__).parent / '.env'
    for _l in _e.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())
    SB = os.environ['SUPABASE_URL']
    K = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
         or os.environ['SUPABASE_KEY'])
    H = {'apikey': K, 'Authorization': f'Bearer {K}'}
    rt = load_team_ratings(SB, H, 2026)
    print(f'loaded ratings for {len(rt)} teams')
    lg = _league_means(rt)
    print('league means:', {k: (round(v, 3) if v else None)
                            for k, v in lg.items()})
    print()
    for away, home, tot in (('Vancouver Canucks', 'Edmonton Oilers', 6.5),
                            ('New York Rangers', 'Boston Bruins', 5.5),
                            ('Florida Panthers', 'Carolina Hurricanes', 6.5)):
        r = project_and_simulate(home, away, rt, close_total=tot)
        if not r:
            print(f'{away} @ {home}: unrated')
            continue
        p, m = r['projection'], r['mc']
        print(f'{away} @ {home}  (market total {tot})')
        print(f'   projected  {away} {p["away_goals"]}  -  '
              f'{home} {p["home_goals"]}   total {p["total"]}')
        print(f'   MC         p(home) {m["mc_p_home"]:.3f}  '
              f'total {m["mc_expected_total"]}  margin {m["mc_expected_margin"]:+.2f}  '
              f'OT {m["mc_ot_rate"]:.1%}  over {m.get("mc_p_over")}')
        print()
