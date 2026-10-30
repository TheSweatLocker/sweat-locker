"""NCAAF alignment + oddscrowd snapshot compute. Same pattern as MLB/NFL;
NCAAF has minimal lens fields until season data matures.

USAGE:
  python compute_align_status_ncaaf.py                  # today ET
  python compute_align_status_ncaaf.py --date 2026-08-22
  python compute_align_status_ncaaf.py --dry-run
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


NCAAF_LENS_FIELDS = {
    'v3': 'projected_spread',
    'v3_total_col': 'projected_total',
    'conf_col': 'signal_confluence_net',
}

NCAAF_EXTRA_SELECT = 'projected_spread,projected_total,signal_confluence_net'


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def run(game_date: str | None = None, dry_run: bool = False, days: int = 8):
    """Sweep game_date forward `days` days. An explicit --date pins one day.

    2026-10-29: this defaulted to TODAY ET and the workflow called it bare.
    See the long note in compute_align_status_nfl.py -- same defect, same
    two consequences (a blank alignment strip and a dark OddsCrowd spread
    feed). College plays most days, which is why NCAAF had 56 of 131 games
    with an oddscrowd_snapshot against NFL's 3 of 179, but a snapshot that
    only lands on the day of the game is too late to publish a pick on.
    """
    from align_status_common import compute_and_write
    if game_date:
        dates = [game_date]
    else:
        base = datetime.strptime(_et_today(), '%Y-%m-%d')
        dates = [(base + timedelta(days=i)).date().isoformat()
                 for i in range(max(1, int(days)))]
    total = 0
    for d in dates:
        print(f'== compute_align_status · NCAAF · {d} ==')
        try:
            total += compute_and_write(
                SB, SB_KEY, sport_code='NCAAF', context_table='ncaaf_game_context',
                game_date=d, lens_fields=NCAAF_LENS_FIELDS,
                extra_select=NCAAF_EXTRA_SELECT, dry_run=dry_run,
            ) or 0
        except Exception as e:
            # One bad date must not cost the rest of the week.
            print(f'  ! {d} failed: {e}')
    print(f'\nSummary: {total} contexts updated across {len(dates)} date(s)')
    return total


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (pins ONE date; default sweeps --days)')
    p.add_argument('--days', type=int, default=8,
                   help='days from today ET to sweep (default 8, matching the '
                        'slate window the external pullers use)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run, days=args.days)
