#!/usr/bin/env python3
"""WHICH model numbers are real equations, and which are the same number again.

== 2026-10-14 - WHY THIS EXISTS ==
audit_panelist_independence.py found 27 pairs of "panelists" taking the same
side of the line 85-100% of the time. This file is the answer to WHY, traced to
the line of code in each case, so the engine can stop seating the same analyst
five times.

The finding is not that the models are correlated. It is that most of them are
ARITHMETICALLY THE SAME NUMBER -- an alias, a sum of the other one's
intermediates, or a simulation seeded from the other one's output. Correlation
you could try to model around. An identity you cannot.

== THE DERIVATIONS, EACH VERIFIED AT A LINE OF CODE ==

NFL -- six nameplates, three real equations:
  projected  REAL. nfl_game_context.py:798-895. team_pts = LEAGUE_AVG_PPG x
             off_idx x opp_def_idx (pass_epa + rush_epa per game), then
             projected_spread = 0.80 x (home_pts - away_pts) + 0.90.
  model      IDENTITY. nfl_game_context.py:896-897 writes
             model_pred_home_points = home_pts and model_pred_away_points =
             away_pts -- the SAME intermediates projected is built from. So
             model_total == projected_total (bar venue_adj) and model_margin is
             an affine transform of projected_spread. Measured: 100% / 96.7%
             side agreement. It is not a second opinion, it is the first
             opinion's working.
  v3         DERIVATIVE. nfl_v3_regression.py:80,160 -- v3_spread =
             projected_spread + spread_delta, where the delta is rest / bye /
             short-week adjustments only. The nudge is far too small to cross
             the line, so v3 takes projected's side 100% of the time.
  v4         DORMANT. nfl_game_context.py:1116-1117 sets v4_spread = None and
             v4_total = None. Its 58 historical rows have sd 0.252 across 9
             values and are never negative, so the home branch of any signal
             reading it can never fire.
  panel      REAL and distinct. Fantasy-aggregated player projections
             (nfl_panel_projection) -- a genuinely different information set.
             Measured 53-61% agreement with the projected bloc.
  mc         REAL and distinct. nfl_mc_simulator.py:8-12 builds its own
             offensive strength from (pass_epa + rush_epa)/games and its own
             expected points per side. Measured 53-61% agreement.

NCAAF -- five nameplates, two real equations:
  projected  REAL. ncaaf_game_context.py:969 -- sp_gap x K_PTS_SP + hfa, with
             an EPA fallback at :1001 when SP+ is missing.
  sp_plus    ALIAS. ncaaf_game_context.py:1180 --
             out['sp_plus_pred_spread'] = round(projected_spread, 2)  # alias
             The comment says alias because it is one. 98.8% agreement.
  epa        REAL and distinct. ncaaf_game_context.py:1174 -- its own
             (h_net_epa - a_net_epa) x K_PTS_EPA + hfa. The one genuine second
             voice in the sport: 50.7% agreement with projected.
  mc         ECHO BY CONSTRUCTION. ncaaf_mc_simulator.py:314-331 takes
             projected_spread and projected_total AS ARGUMENTS and sets
             base_home = (projected_total + projected_spread) / 2. A symmetric
             simulation around a point estimate returns that point estimate, so
             mc_expected_margin converges to projected_spread. 99.6% agreement
             is arithmetic, not coincidence.
  model      IDENTITY. ncaaf_game_context.py:1136 --
             model_pred_home_points = total x home_share.

NHL -- three nameplates, one equation:
  projected / goals / mc all come out of the one Elo-based predictor
  (nhl_game_context.py:158-160 writes projected_home_goals, projected_away_goals
  and projected_total from the same `p`). All three pairs agree 100.0%.

MLB is the counter-example that proves the fix works. enrich_monte_carlo.py:40
and :70-78 record that MC was migrated on 2026-07-23 off "thin
Poisson-on-projections" -- which is exactly what NCAAF still does -- onto a
per-inning simulator with SP/BP form, hand splits, park, weather and umpire,
with the commit note "MC is now an INDEPENDENT lens, not a v4-echo." MLB's
panelists show no duplicate pair.

== WHAT THIS CHANGES ==
An entry marked IDENTITY, ALIAS, DERIVATIVE or ECHO must not be counted as an
independent vote by any consensus, ensemble or agreement gate. It may still be
displayed. REAL entries are the only ones that may be seated.

Consequence to state plainly: NFL's v3 at 58.3% / +11.4% and projected at
57.6% / +10.0% are THE SAME PICK on every game. There is no ignored star
panelist -- there is one model at roughly 58% on 60 picks, wearing two
nameplates.

USAGE
    python panelist_registry.py            # the map, with seat counts
    python panelist_registry.py --seats    # just who may vote
"""
from __future__ import annotations

import argparse
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

#: kind -> may it count as an independent vote
REAL = 'REAL'
IDENTITY = 'IDENTITY'       # literally the same arithmetic
ALIAS = 'ALIAS'             # assigned from the other column
DERIVATIVE = 'DERIVATIVE'   # other model + a nudge too small to cross the line
ECHO = 'ECHO'               # simulation seeded from another model's output
DORMANT = 'DORMANT'         # writes NULL today

SEATABLE = (REAL,)

#: (sport, panelist) -> (kind, evidence, agreement note)
PANELISTS = {
    ('NFL', 'projected'): (
        REAL, 'nfl_game_context.py:798-895 off/def EPA index -> points',
        'the base everything else is built from'),
    ('NFL', 'model'): (
        IDENTITY, 'nfl_game_context.py:896-897 writes projected\'s own '
        'home_pts/away_pts intermediates', '100% total / 96.7% margin'),
    ('NFL', 'v3'): (
        DERIVATIVE, 'nfl_v3_regression.py:80,160 projected_spread + '
        'rest/bye/short-week delta', '100% margin'),
    ('NFL', 'v4'): (
        DORMANT, 'nfl_game_context.py:1116-1117 sets both to None',
        'never negative in 58 historical rows; home branch cannot fire'),
    ('NFL', 'panel'): (
        REAL, 'fantasy-aggregated player projections (nfl_panel_projection)',
        '53-61% — a distinct voice'),
    ('NFL', 'mc'): (
        REAL, 'nfl_mc_simulator.py:8-12 own (pass_epa+rush_epa)/games',
        '53-61% — a distinct voice'),

    ('NCAAF', 'projected'): (
        REAL, 'ncaaf_game_context.py:969 sp_gap x K_PTS_SP + hfa',
        'the base'),
    ('NCAAF', 'sp_plus'): (
        ALIAS, 'ncaaf_game_context.py:1180 = round(projected_spread, 2)',
        '98.8%'),
    ('NCAAF', 'epa'): (
        REAL, 'ncaaf_game_context.py:1174 own (h_net_epa-a_net_epa) x K_PTS_EPA',
        '50.7% — the one genuine second voice'),
    ('NCAAF', 'mc'): (
        ECHO, 'ncaaf_mc_simulator.py:314-331 takes projected_spread + '
        'projected_total as arguments', '99.6%'),
    ('NCAAF', 'model'): (
        IDENTITY, 'ncaaf_game_context.py:1136 total x home_share', '84-100%'),

    ('NHL', 'projected'): (
        REAL, 'nhl_game_context.py:158-160 Elo-based predictor', 'the base'),
    ('NHL', 'goals'): (
        IDENTITY, 'nhl_game_context.py:158-160 same `p` as projected_total',
        '100.0%'),
    ('NHL', 'mc'): (
        ECHO, 'same Elo projection, simulated', '100.0%'),

    ('MLB', 'projected'): (REAL, 'game_context projection', 'base'),
    ('MLB', 'mc'): (
        REAL, 'enrich_monte_carlo.py:40 per-inning simulator, migrated off '
        'Poisson-on-projections 2026-07-23', 'no duplicate pair in MLB'),
    ('MLB', 'jerry'): (REAL, 'jerry_pred_total', 'no duplicate pair'),
    ('MLB', 'panel'): (REAL, 'panel_implied_total', 'no duplicate pair'),
    ('MLB', 'model'): (REAL, 'model_pred_total', 'no duplicate pair'),
}


def seats(sport: str) -> list[str]:
    """Panelists in this sport that may count as independent votes."""
    return sorted(p for (s, p), (k, _e, _n) in PANELISTS.items()
                  if s == sport and k in SEATABLE)


def kind(sport: str, panelist: str) -> str | None:
    e = PANELISTS.get((sport, panelist))
    return e[0] if e else None


def may_vote(sport: str, panelist: str) -> bool:
    return kind(sport, panelist) in SEATABLE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--seats', action='store_true')
    a = ap.parse_args()
    sports = ['NFL', 'NCAAF', 'NHL', 'MLB']
    if a.seats:
        for s in sports:
            print(f'{s:<7} {", ".join(seats(s)) or "NONE"}')
        return 0
    print('PANELIST REGISTRY — which model numbers are real equations.\n')
    for s in sports:
        rows = [(p, v) for (sp, p), v in PANELISTS.items() if sp == s]
        real = [p for p, (k, _, _) in rows if k in SEATABLE]
        print(f'{"="*78}\n{s}   {len(rows)} nameplates, {len(real)} seatable '
              f'voice(s): {", ".join(sorted(real)) or "NONE"}')
        for p, (k, ev, note) in sorted(rows, key=lambda x: (x[1][0] != REAL, x[0])):
            mark = ' ' if k in SEATABLE else '*'
            print(f'  {mark}{p:<11}{k:<11}{note}')
            print(f'   {"":<11}{"":<11}{ev}')
    print(f'\n* = must NOT be counted as an independent vote by any consensus, '
          f'ensemble or agreement gate. Display is fine.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
