"""NBA forward schedule + live lines from The Odds API.

2026-09-21: created. nba_game_results had ZERO forward games, so no
slate existed for context, props, externals or the game card.

2026-10-28: "NBA needs no alias map — Odds API names match our stored
names exactly (verified 27/27 on 2026-09-21)" was wrong, and the count
says why: 27 of 30 teams. The Clippers are one of the three it never
saw. The Odds API says "Los Angeles Clippers"; our context and results
tables, the ESPN schedule, nba_elo and compute_margin_strength all say
"LA Clippers", so the canonical-id lookup missed and every Clippers game
got a SECOND, permanently unresolvable row in nba_game_results.

Sign convention: close_spread is home-perspective, NEGATIVE = home
favored.

USAGE
  python nba_odds_pull.py
  python nba_odds_pull.py --dry-run
"""
import argparse

from nba_data_client import get_schedule as _nba_schedule

from odds_pull_core import OddsPuller

PULLER = OddsPuller(
    sport_code='NBA',
    odds_sport='basketball_nba',
    results_table='nba_game_results',
    id_prefix='nba',
    spread_col='close_spread',
    total_col='close_total',
    season='2026-27',
    team_map=None,
    # The DB's spelling wins. Same alias already lives in
    # compute_margin_strength.NBA_ALIASES, nba_elo and
    # backfill_historical_odds_hoops_hockey — this was the fourth copy
    # needed and the only one missing.
    name_aliases={'Los Angeles Clippers': 'LA Clippers'},
    write_abbrev=False,
    # Adopt the league's canonical game id so results and context
    # share a key; without it the two tables had 0 id overlap.
    schedule_fn=_nba_schedule,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    PULLER.run(dry_run=ap.parse_args().dry_run)


if __name__ == '__main__':
    main()
