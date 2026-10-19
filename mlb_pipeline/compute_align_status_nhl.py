"""NHL alignment + oddscrowd snapshot compute.

══ 2026-10-19 · WHY THIS FILE DID NOT EXIST ══
ANDY asked why The Fade had no plays for other sports. The answer was a
missing link in a five-step chain, and this was the missing step:

    oddscrowd pull  ->  compute_align_status_<sport>  ->
    context.oddscrowd_snapshot  ->  write_line_snapshot  ->
    line_snapshot  ->  The Fade

align_status_common is already sport-agnostic and its docstring lists the
callers: MLB, NFL, NCAAF, NCAAB. There was no NBA and no NHL. So those two
sports never got oddscrowd_snapshot written onto their context rows, which
means write_line_snapshot had nothing to append, which means line_snapshot
held ZERO NHL rows:

    line_snapshot public splits:  MLB 16,807 · NCAAF 743 · NFL 549
                                  NBA 0 · NHL 0 · NCAAB 0 · UFC 0

The Fade needs a public ticket/money split to fade, so with MLB finished it
could only ever fire on NFL (Thu/Sun/Mon) and NCAAF (Sat) - empty three or
four nights a week, including every night of the NBA season.

Thin by design: the alignment logic is sport-invariant and lives in
align_status_common. Only the lens fields differ, and every column named
below was verified present on nhl_game_context before this was written (125
columns) rather than assumed - naming a column PostgREST does not know
turns the whole read into a 400 that reads back as an empty table.

USAGE:
  python compute_align_status_nhl.py                  # today ET
  python compute_align_status_nhl.py --date 2026-10-21
  python compute_align_status_nhl.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']


# NHL prices the handicap as close_puckline (a fixed +/-1.5), not
# close_spread, so the total lens is the useful one here.
NHL_LENS_FIELDS = {
    'v3': 'projected_spread',
    'v3_total_col': 'projected_total',
    'conf_col': 'signal_confluence_net',
}

NHL_EXTRA_SELECT = 'projected_spread,projected_total,signal_confluence_net'


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def run(game_date: str | None = None, dry_run: bool = False):
    game_date = game_date or _et_today()
    print(f'== compute_align_status · NHL · {game_date} ==')
    from align_status_common import compute_and_write
    updated = compute_and_write(
        SB, SB_KEY, sport_code='NHL', context_table='nhl_game_context',
        game_date=game_date, lens_fields=NHL_LENS_FIELDS,
        extra_select=NHL_EXTRA_SELECT, dry_run=dry_run,
    )
    print(f'\nSummary: {updated} contexts updated')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (defaults to today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)
