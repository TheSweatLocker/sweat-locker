"""NBA external picks puller.

2026-09-21: created. There was no NBA external puller at all, even
though externals_oddscrowd / externals_scoresandodds / externals_pickdawgz
were already parameterised for nba and name this file as their caller.

All scaffolding lives in externals_pro_core so NBA and NHL cannot drift
apart the way the five college/pro pullers did.

USAGE
  python pull_externals_nba.py
  python pull_externals_nba.py --date 2026-10-08
  python pull_externals_nba.py --source oddscrowd
  python pull_externals_nba.py --dry-run
"""
import argparse

from externals_pro_core import SportPuller

PULLER = SportPuller(
    sport='NBA',
    ctx_table='nba_game_context',
    results_table='nba_game_results',
    oddscrowd_sport_slug='basketball',
    league_slug='nba',
    horizon_days=2,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--source', default=None,
                    help='oddscrowd | scoresandodds | pickdawgz')
    ap.add_argument('--triggered-by', default='manual')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    PULLER.run(game_date=a.date,
               sources=[a.source] if a.source else None,
               triggered_by=a.triggered_by, dry_run=a.dry_run)


if __name__ == '__main__':
    main()
