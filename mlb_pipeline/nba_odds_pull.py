"""NBA forward schedule + live lines from The Odds API.

2026-09-21: created. nba_game_results had ZERO forward games, so no
slate existed for context, props, externals or the game card.

NBA needs no alias map — Odds API names match our stored names exactly
(verified 27/27 on 2026-09-21). Sign convention: close_spread is
home-perspective, NEGATIVE = home favored.

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
