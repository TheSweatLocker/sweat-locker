"""NBA results resolver (2026-08-17).

Grades completed NBA games from ESPN scoreboard. For each finalized
game on `game_date`:
  1. Pull final scores + OT flag
  2. Compute spread_result vs close_spread
  3. Compute total_result vs close_total
  4. Compute home_win
  5. Upsert to nba_game_results

CLI:
  python nba_resolve_results.py                  # yesterday ET
  python nba_resolve_results.py --date 2024-11-10
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

sys.path.insert(0, str(Path(__file__).parent))
from nba_data_client import get_scoreboard


def _today_et() -> date:
    """TRUE Eastern date, not a fixed offset.

    ══ 2026-11-05 · A HARDCODED -4 IS WRONG FOR 127 DAYS A YEAR ══
    This was `now_utc - timedelta(hours=4, days=1)`. DST ended
    2026-11-01, so ET is -5 now and the two disagree in the
    04:00-04:59 UTC hour — which is exactly when the nightly runs.
    Same class as feedback_hardcoded_et_offset_breaks_in_est; this
    file was not among the 16 triaged there. ZoneInfo is the repo's
    established idiom (backfill_public_receipts, compute_surface_records).
    """
    now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo('America/New_York')).date()
    except Exception:                                          # noqa: BLE001
        return (now - timedelta(hours=5)).date()


def _window_et(days: int) -> list[str]:
    """The last `days` ET dates, oldest first. days=1 is yesterday only.

    ══ 2026-11-05 · A ONE-DAY WINDOW CANNOT RECOVER A MISSED NIGHT ══
    This resolver took no window at all: main() passed a single date and
    a night that was skipped stayed dark forever. On 2026-11-05 that was
    not hypothetical — nba_game_results held 14 rows for 11-04 with
    every score NULL, which left every 11-04 NBA prop ungraded, all 16
    NBA game_read receipts ungraded, the Houston POTD ungraded, and
    every NBA surface record frozen at 11-02 while still being rendered
    as a d7/d30 window. The resolver itself was fine: a dry run returned
    14 of 14 finals from ESPN the next morning.

    So the default is a 3-day look-back, matching the window every other
    grader here already takes (grade_prop_playbook --backfill 3,
    signal_attribution_grade --days 7). Re-resolving a date we already
    hold is cheap and idempotent — the upsert writes the same finals —
    and it is the only thing that makes a skipped night self-healing.
    """
    today = _today_et()
    return [(today - timedelta(days=n)).isoformat()
            for n in range(days, 0, -1)]


def _grade_spread(home_score: int, away_score: int, spread: float | None) -> str | None:
    """close_spread is HOME perspective. Home covered if
    (home_score + spread) > away_score."""
    if spread is None: return None
    margin = (home_score - away_score) + float(spread)
    if margin > 0.001: return 'home_covered'
    if margin < -0.001: return 'away_covered'
    return 'push'


def _grade_total(home_score: int, away_score: int, total: float | None) -> str | None:
    if total is None: return None
    t = home_score + away_score
    if t > float(total): return 'over'
    if t < float(total): return 'under'
    return 'push'


def resolve(game_date: str, dry_run: bool = False) -> int:
    print(f'=== nba_resolve_results · {game_date} ===')
    finals = get_scoreboard(game_date)
    print(f'  {len(finals)} finalized games')
    if not finals: return 0

    # Look up ctx to grab lines
    game_ids = [f['game_id'] for f in finals]
    r = requests.get(f'{SB}/rest/v1/nba_game_context'
                     f'?game_id=in.({",".join(game_ids)})'
                     '&select=game_id,close_spread,close_total,home_ml_close,away_ml_close,season',
                     headers=H_READ, timeout=15)
    ctx_by_gid = {c['game_id']: c for c in (r.json() if r.status_code == 200 else [])}

    # == 2026-10-28 . FALL BACK TO THE LINE ALREADY STORED ==
    # The grades below are computed from the CTX line, so a game whose ctx
    # row has no close_spread got spread_result=None even when
    # nba_game_results already held a perfectly good line. That is how the
    # three Clippers games sat with a score, a restored -4.5/-9.0/-16.0 and
    # still no spread_result. ctx wins when it has a value; the stored row
    # is used when it does not. Neither is invented.
    r2 = requests.get(f'{SB}/rest/v1/nba_game_results'
                      f'?game_id=in.({",".join(game_ids)})'
                      '&select=game_id,close_spread,close_total,'
                      'close_home_ml,close_away_ml',
                      headers=H_READ, timeout=15)
    stored_by_gid = {c['game_id']: c
                     for c in (r2.json() if r2.status_code == 200 else [])}

    now_iso = datetime.now(timezone.utc).isoformat()
    written = 0
    for f in finals:
        gid = f['game_id']
        hs = f['home_score']; as_ = f['away_score']
        if hs is None or as_ is None: continue
        ctx = ctx_by_gid.get(gid, {})
        _st = stored_by_gid.get(gid, {})
        spread = ctx.get('close_spread')
        if spread is None:
            spread = _st.get('close_spread')
        total = ctx.get('close_total')
        if total is None:
            total = _st.get('close_total')

        payload = {
            'game_id':      gid,
            'game_date':    game_date,
            'season':       ctx.get('season'),
            'home_team':    f.get('home_team'),
            'away_team':    f.get('away_team'),
            'home_abbrev':  f.get('home_abbrev'),
            'away_abbrev':  f.get('away_abbrev'),
            'home_score':   hs,
            'away_score':   as_,
            'total_points': hs + as_,
            'home_win':     hs > as_,
            'went_to_ot':   f.get('went_to_ot', False),
            'close_spread': spread,
            'close_total':  total,
            'close_home_ml': (ctx.get('home_ml_close')
                              if ctx.get('home_ml_close') is not None
                              else _st.get('close_home_ml')),
            'close_away_ml': (ctx.get('away_ml_close')
                              if ctx.get('away_ml_close') is not None
                              else _st.get('close_away_ml')),
            'spread_result': _grade_spread(hs, as_, spread),
            'total_result':  _grade_total(hs, as_, total),
            'resolved_at':   now_iso,
        }

        # == 2026-10-28 . A RESOLVER MUST NOT BLANK A CLOSING LINE ==
        # These four close_* values are copied out of nba_game_context, and
        # when ctx has none the copy wrote NULL straight over whatever was
        # already stored. nba_game_context holds a close_spread on only 57
        # of 81 recent rows, so a resolver run DESTROYED good lines.
        #
        # Caught the hard way: the Clippers phantom repair merged four line
        # fields onto three real result rows (rows holding both a score and
        # a close line went 59 -> 62), then running this resolver on those
        # same dates put them back to NULL and the count fell to 52.
        #
        # This runs AFTER the game is final, so there is no case where
        # blanking a closing line is right -- the line is frozen by then. A
        # None here means 'ctx does not have it', not 'it is not true'.
        # Scores, results and resolved_at are this script's OWN output and
        # are always written, including a legitimate None.
        for _k in ('close_spread', 'close_total',
                   'close_home_ml', 'close_away_ml'):
            if payload.get(_k) is None:
                payload.pop(_k, None)

        note = ''
        if payload['spread_result']: note += f' sp={payload["spread_result"]}'
        if payload['total_result']:  note += f' tot={payload["total_result"]}'
        print(f'  {f.get("away_team","?"):<25} {as_}-{hs} {f.get("home_team","?"):<25} '
              f'{"OT" if f.get("went_to_ot") else "REG":<3}{note}')

        if dry_run: written += 1; continue
        pr = requests.post(f'{SB}/rest/v1/nba_game_results?on_conflict=game_id',
                           headers=H_WRITE, json=[payload], timeout=15)
        if pr.status_code in (200, 201, 204): written += 1
        else: print(f'    ✗ upsert failed: {pr.status_code} {pr.text[:200]}')

    print(f'\n  {"[DRY] " if dry_run else ""}wrote {written}/{len(finals)} results')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='resolve exactly this ET date')
    p.add_argument('--days', type=int, default=3,
                   help='ET days back to re-resolve when --date is absent '
                        '(default 3, so one skipped night self-heals)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    dates = [args.date] if args.date else _window_et(max(1, args.days))
    wrote = 0
    for d in dates:
        # One date failing must not abandon the rest of the window: the
        # whole point of the window is recovering the night that broke.
        try:
            wrote += resolve(d, dry_run=args.dry_run) or 0
        except Exception as e:                                 # noqa: BLE001
            print(f'  !! {d}: {type(e).__name__}: {e} — continuing')
    if len(dates) > 1:
        print(f'=== window {dates[0]}..{dates[-1]} · {wrote} result row(s) '
              f'{"would be " if args.dry_run else ""}written ===')
    return 0


if __name__ == '__main__':
    main()
