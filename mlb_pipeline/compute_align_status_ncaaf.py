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


def run(game_date: str | None = None, dry_run: bool = False):
    game_date = game_date or _et_today()
    print(f'== compute_align_status · NCAAF · {game_date} ==')
    from align_status_common import compute_and_write
    updated = compute_and_write(
        SB, SB_KEY, sport_code='NCAAF', context_table='ncaaf_game_context',
        game_date=game_date, lens_fields=NCAAF_LENS_FIELDS,
        extra_select=NCAAF_EXTRA_SELECT, dry_run=dry_run,
    )
    print(f'\nSummary: {updated} contexts updated')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (defaults to today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)
