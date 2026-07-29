"""NFL alignment + oddscrowd snapshot compute. Same pattern as MLB version;
NFL just has fewer lens fields (v3 projected_spread + confluence).

USAGE:
  python compute_align_status_nfl.py                  # today ET
  python compute_align_status_nfl.py --date 2026-09-04
  python compute_align_status_nfl.py --dry-run
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


# NFL context has fewer lens fields than MLB (Phase 1). Extend as more
# models land (v4 XGBoost, jerry equivalent, MC ensemble).
NFL_LENS_FIELDS = {
    'v3':               'projected_spread',
    'v3_total_col':     'projected_total',
    'conf_col':         'signal_confluence_net',
}

NFL_EXTRA_SELECT = 'projected_spread,projected_total,signal_confluence_net,model_pred_home_points,model_pred_away_points'


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def run(game_date: str | None = None, dry_run: bool = False):
    game_date = game_date or _et_today()
    print(f'== compute_align_status · NFL · {game_date} ==')
    from align_status_common import compute_and_write
    updated = compute_and_write(
        SB, SB_KEY, sport_code='NFL', context_table='nfl_game_context',
        game_date=game_date, lens_fields=NFL_LENS_FIELDS,
        extra_select=NFL_EXTRA_SELECT, dry_run=dry_run,
    )
    print(f'\nSummary: {updated} contexts updated')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (defaults to today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)
