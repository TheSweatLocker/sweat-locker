"""NHL forward schedule + live lines from The Odds API.

2026-09-21: created. nhl_game_results had ZERO forward games.

NHL DOES need a map. nhl_data_client writes the NHL API's `placeName`,
which is the CITY, so historical rows hold "Boston" / "New York" /
"Los Angeles" — meaning Rangers and Islanders are indistinguishable and
nothing joins to an odds feed (0 of 32 matched). This puller writes the
FULL team name plus the abbrev, which is unambiguous.

Sign convention: close_puckline is home-perspective, NEGATIVE = home
favored.

USAGE
  python nhl_odds_pull.py
  python nhl_odds_pull.py --dry-run
"""
import argparse

from nhl_data_client import get_schedule as _nhl_schedule

from odds_pull_core import NHL_TEAMS, OddsPuller

PULLER = OddsPuller(
    sport_code='NHL',
    odds_sport='icehockey_nhl',
    results_table='nhl_game_results',
    id_prefix='nhl',
    spread_col='close_puckline',
    total_col='close_total',
    season=None,            # nhl_game_results has no season column
    team_map=NHL_TEAMS,
    write_abbrev=False,     # nhl_game_results has no abbrev columns
    # Adopt the NHL API's canonical game id so results and context share
    # a key. Without it the two tables had 0 id overlap — the same split
    # that left NFL Vault Match with zero graded games since launch.
    schedule_fn=_nhl_schedule,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    PULLER.run(dry_run=ap.parse_args().dry_run)


if __name__ == '__main__':
    main()
