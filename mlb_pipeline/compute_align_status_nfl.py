"""NFL alignment + oddscrowd snapshot compute. Same pattern as MLB version;
NFL just has fewer lens fields (v3 projected_spread + confluence).

USAGE:
  python compute_align_status_nfl.py                  # today ET + 8 days
  python compute_align_status_nfl.py --date 2026-09-04   # that date only
  python compute_align_status_nfl.py --days 1          # today only (old way)
  python compute_align_status_nfl.py --dry-run

== 2026-10-29 · A WEEKLY SPORT ON A DAILY DEFAULT ==========================
This defaulted to TODAY ET and the workflow called it bare. NFL plays
Thursday, Sunday and Monday, so on nearly every run it computed alignment
for a date with no games -- while the step directly above it in the same
workflow already sweeps a week (nfl_goat_composite.py --days 8).

Measured on 2026-10-29, forward games since 10-20:

    align_status non-null        NFL   0 of 179      NCAAF   0 of 131
    oddscrowd_snapshot non-null  NFL   3 of 179      NCAAF  56 of 131

compute_and_write produces BOTH fields, so one date default caused two
separate outages:

  * the app's alignment strip -- the chip row that says whether the signals
    agree with the pick -- rendered blank on every football game;
  * oddscrowd_snapshot stayed null, and write_line_snapshot flattens that
    column into line_snapshot, so the OddsCrowd spread feed was dark. That
    feed is the source of the only validated football spread edge we own
    (fade public bets>=65: 58.9%, n=107, ROI +12.4%, holds both halves), so
    the edge could not reach a slate even though the scraper worked.

NCAAF's 56 vs NFL's 3 is the same defect, not a different one: college
plays most days of the week, so "today" happened to catch some games. By
the time a game IS today the fade is useless anyway -- a pick has to be
published before kickoff.

The scrape itself was never broken. Verified by running it live: the NFL
listing carries all 10 of Sunday's games and fetch_oddscrowd_generic
returned 21 picks across 7 games with usable splits.
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

NFL_EXTRA_SELECT = 'projected_spread,projected_total,signal_confluence_net,model_pred_home_points,model_pred_away_points,primary_play'


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def run(game_date: str | None = None, dry_run: bool = False, days: int = 8):
    """Sweep game_date forward `days` days. An explicit --date pins one day."""
    from align_status_common import compute_and_write
    if game_date:
        dates = [game_date]
    else:
        base = datetime.strptime(_et_today(), '%Y-%m-%d')
        dates = [(base + timedelta(days=i)).date().isoformat()
                 for i in range(max(1, int(days)))]
    total = 0
    for d in dates:
        print(f'== compute_align_status · NFL · {d} ==')
        try:
            total += compute_and_write(
                SB, SB_KEY, sport_code='NFL', context_table='nfl_game_context',
                game_date=d, lens_fields=NFL_LENS_FIELDS,
                extra_select=NFL_EXTRA_SELECT, dry_run=dry_run,
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
