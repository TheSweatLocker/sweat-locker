"""MLB alignment + oddscrowd snapshot compute. Thin wrapper around
align_status_common.compute_and_write with MLB-specific lens field map.

USAGE:
  python compute_align_status.py                  # today ET
  python compute_align_status.py --date 2026-07-29
  python compute_align_status.py --dry-run
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


MLB_LENS_FIELDS = {
    'panel': 'panel_implied_margin',
    'jerry': 'jerry_pred_spread',
    'v3':    'projected_spread',
    'v4':    'model_pred_spread',
    'mc_json_col':   'mc_probabilities',
    'mc_margin_key': 'mc_expected_margin',
    'mc_total_key':  'mc_expected_total',
    'conf_col':      'signal_confluence_net',
    'panel_total_col': 'panel_implied_total',
    'jerry_total_col': 'jerry_pred_total',
    'v3_total_col':    'projected_total',
    'v4_total_col':    'model_pred_total',
}

# Extra columns needed for lens computation on top of the base select
MLB_EXTRA_SELECT = ','.join([
    'panel_implied_margin', 'jerry_pred_spread', 'projected_spread', 'model_pred_spread',
    'mc_probabilities', 'signal_confluence_net',
    'panel_implied_total', 'jerry_pred_total', 'projected_total', 'model_pred_total',
])


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def run(game_date: str | None = None, dry_run: bool = False):
    game_date = game_date or _et_today()
    print(f'== compute_align_status · MLB · {game_date} ==')
    from align_status_common import compute_and_write
    updated = compute_and_write(
        SB, SB_KEY, sport_code='MLB', context_table='mlb_game_context',
        game_date=game_date, lens_fields=MLB_LENS_FIELDS,
        extra_select=MLB_EXTRA_SELECT, dry_run=dry_run,
    )
    print(f'\nSummary: {updated} contexts updated')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (defaults to today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)
