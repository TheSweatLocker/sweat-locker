"""External-picks resolver — grade every source's picks against outcomes.

Sport-parameterized: --sport MLB (default) / NFL / NCAAB. Joins
external_picks to the sport-specific game_results table by game_id and
grades:
  - ml (moneyline)   : W/L based on home_win vs pick_side
  - spread / rl      : W/L/P from spread_result (source-agnostic — the
                       sport's game_results already normalizes to
                       home_covered / away_covered / push)
  - total (o/u)      : W/L/P from total_result (over / under / push)
  - prop             : deferred to prop-resolver join (writes
                       resolver_note='prop_pending')
  - sharp_signal     : informational, not gradeable (skipped)

Idempotent — skips rows already resolved unless --force-regrade.

Runs nightly after the sport's resolver ships graded outcomes:
  MLB:   after resolve_potd.py + resolve_props.py
  NFL:   after resolve_nfl_results.py (Tue + Mon)
  NCAAB: after ncaab_resolver.py (post-tip 3hr window)

USAGE:
    python resolve_externals.py                    # grade all MLB ungraded
    python resolve_externals.py --sport NFL        # grade NFL only
    python resolve_externals.py --sport MLB --days 3 # only last 3 days
    python resolve_externals.py --force-regrade    # re-grade resolved
    python resolve_externals.py --dry-run
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# Per-sport results-table + column mapping.
# spread_result / total_result values are UNIFIED across sports at the
# resolver level: 'home_covered' | 'away_covered' | 'push' for spreads;
# 'over' | 'under' | 'push' for totals. If a sport's resolver writes
# different literals, add a normalization step here.
SPORT_CONFIG = {
    'MLB': {
        'results_table': 'mlb_game_results',
        'select_cols': ('game_id,home_score,away_score,total_runs,home_win,'
                        'spread_result,total_result,close_spread,close_total'),
        'spread_home_ok': ('home_covered',),
        'spread_away_ok': ('away_covered',),
        'total_over_ok':  ('over',),
        'total_under_ok': ('under',),
        'push_vals':      ('push',),
    },
    'NFL': {
        'results_table': 'nfl_game_results',
        'select_cols': ('game_id,home_score,away_score,total_points,home_win,'
                        'spread_result,total_result,close_spread,close_total'),
        'spread_home_ok': ('home_covered',),
        'spread_away_ok': ('away_covered',),
        'total_over_ok':  ('over',),
        'total_under_ok': ('under',),
        'push_vals':      ('push',),
    },
    'NCAAB': {
        'results_table': 'ncaab_game_context',   # placeholder — swap when
                                                  # ncaab_game_results exists
        'select_cols': ('game_id,home_score,away_score,total_points,home_win,'
                        'spread_result,total_result,close_spread,close_total'),
        'spread_home_ok': ('home_covered',),
        'spread_away_ok': ('away_covered',),
        'total_over_ok':  ('over',),
        'total_under_ok': ('under',),
        'push_vals':      ('push',),
    },
    # 2026-09-01: NCAAF added per 9/1 audit finding — resolve_externals
    # was called from ncaaf_pipeline.yml but SPORT_CONFIG had no NCAAF
    # entry → guard at line 288 returned immediately → all NCAAF external
    # picks stayed result=NULL → compute_external_source_records skipped
    # them → app showed empty W-L chips. Root cause fixed at the config
    # level (not per-call band-aid).
    'NCAAF': {
        'results_table': 'ncaaf_game_results',
        'select_cols': ('game_id,home_score,away_score,total_points,home_win,'
                        'spread_result,total_result,close_spread,close_total'),
        'spread_home_ok': ('home_covered',),
        'spread_away_ok': ('away_covered',),
        'total_over_ok':  ('over',),
        'total_under_ok': ('under',),
        'push_vals':      ('push',),
    },
}


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def fetch_ungraded_picks(sport: str, days: Optional[int], force_regrade: bool) -> list:
    """Return external_picks rows needing grading.

    Filters out prop + sharp_signal (not gradeable via game_results join).
    """
    filt = '&surface=in.(ml,spread,rl,total)'
    if not force_regrade:
        filt += '&result=is.null'
    if days:
        cutoff = (_et_now().date() - timedelta(days=days)).isoformat()
        filt += f'&game_date=gte.{cutoff}'
    filt += f'&sport=eq.{sport}'

    # Paginate — PostgREST caps at 1000/req. 2026-08-28: --force-regrade
    # over 30d was silently truncating a 4k-row universe to the first 1000,
    # leaving ~3k SBR total rows stuck at their stale 8/27 grades.
    out = []
    for off in range(0, 50000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/external_picks?'
            f'select=id,game_id,sport,game_date,source,surface,pick_side,pick_line'
            f'{filt}&limit=1000&offset={off}',
            headers=H_READ, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list): break
        out.extend(chunk)
        if len(chunk) < 1000: break
    return out


def fetch_result_map(sport: str, game_ids: list) -> dict:
    """Batch-fetch outcome rows keyed by game_id."""
    if not game_ids:
        return {}
    cfg = SPORT_CONFIG[sport]
    out = {}
    for i in range(0, len(game_ids), 100):
        chunk = game_ids[i:i + 100]
        ids_str = ','.join(f'"{g}"' for g in chunk)
        r = requests.get(
            f'{SB}/rest/v1/{cfg["results_table"]}?game_id=in.({ids_str})'
            f'&select={cfg["select_cols"]}',
            headers=H_READ, timeout=15,
        )
        if r.status_code != 200:
            print(f'  ⚠ result fetch {r.status_code}: {r.text[:120]}')
            continue
        for row in r.json():
            out[row['game_id']] = row
    return out


def grade_pick(pick: dict, result: dict, cfg: dict) -> tuple:
    """Return (grade, actual_value, note).

    grade: 'W' | 'L' | 'P' | None (None = still pending / not gradeable)
    actual_value: numeric for total picks (final total_runs/points), margin
                  for spread picks; None otherwise.
    note: optional resolver_note for edge cases.
    """
    if not result:
        return None, None, 'game_not_found'

    home_score = result.get('home_score')
    away_score = result.get('away_score')
    if home_score is None or away_score is None:
        return None, None, 'game_pending'   # game not yet finished

    surface = (pick.get('surface') or '').lower()
    side = (pick.get('pick_side') or '').upper()
    margin = int(home_score) - int(away_score)
    total_pts = result.get('total_runs') or result.get('total_points') \
        or (int(home_score) + int(away_score))

    # MONEYLINE
    if surface == 'ml':
        home_win = bool(result.get('home_win'))
        if side == 'HOME':
            return ('W' if home_win else 'L', float(margin), None)
        if side == 'AWAY':
            return ('L' if home_win else 'W', float(margin), None)
        return None, None, 'invalid_pick_side_ml'

    # SPREAD / RUN LINE
    if surface in ('spread', 'rl'):
        sr = result.get('spread_result')
        if not sr:
            # Fallback: derive from margin + close_spread if resolver didn't fill
            cs = result.get('close_spread')
            if cs is not None:
                # MLB convention: positive close_spread = home is DOG
                # (getting cs runs). Home covers when margin > -cs
                # (i.e., loses by fewer than cs runs OR wins outright).
                # This handles both MLB and NFL/nflverse if the sport's
                # backfill_results normalized spread_result properly.
                if margin > -float(cs):   sr = 'home_covered'
                elif margin < -float(cs): sr = 'away_covered'
                else:                     sr = 'push'
            else:
                return None, None, 'no_spread_result'
        if sr in cfg['push_vals']:
            return 'P', float(margin), None
        if side == 'HOME':
            return ('W' if sr in cfg['spread_home_ok'] else 'L',
                    float(margin), None)
        if side == 'AWAY':
            return ('W' if sr in cfg['spread_away_ok'] else 'L',
                    float(margin), None)
        return None, None, 'invalid_pick_side_spread'

    # TOTAL
    if surface == 'total':
        # 2026-08-12: grade against pick.pick_line (what the source
        # actually tracked), NOT close_total. Prior version used the
        # game_results.total_result field which is derived from close_
        # total — but external sources like oddscrowd track picks at
        # their own snapshot time, so their pick_line often DIFFERS
        # from close_total after book movement. Spot-check of 40 rows
        # showed 47% mismatch rate (19-17 correct) using close_total.
        # Grading against pick.pick_line makes grades match reality.
        pick_line = pick.get('pick_line')
        if pick_line is None:
            # No line stored → fall back to close_total (legacy path)
            tr = result.get('total_result')
            if not tr:
                ct = result.get('close_total')
                if ct is not None:
                    if total_pts > float(ct):   tr = 'over'
                    elif total_pts < float(ct): tr = 'under'
                    else:                       tr = 'push'
                else:
                    return None, None, 'no_total_result_no_pick_line'
        else:
            # Grade against the line the pick was made against
            try:
                pline = float(pick_line)
            except (TypeError, ValueError):
                return None, None, 'invalid_pick_line'
            if total_pts > pline:   tr = 'over'
            elif total_pts < pline: tr = 'under'
            else:                   tr = 'push'

        # 2026-08-27 case-normalize: game_results.total_result is stored as
        # 'Over'/'Under'/'Push' (title case) but the SPORT_CONFIG buckets are
        # lowercase. Silent bug — every OVER/UNDER pick was marked L because
        # 'Under' not in ('under',). Normalize before the containment check.
        if isinstance(tr, str): tr = tr.lower()
        if tr in cfg['push_vals']:
            return 'P', float(total_pts), None
        if side == 'OVER':
            return ('W' if tr in cfg['total_over_ok'] else 'L',
                    float(total_pts), None)
        if side == 'UNDER':
            return ('W' if tr in cfg['total_under_ok'] else 'L',
                    float(total_pts), None)
        return None, None, 'invalid_pick_side_total'

    return None, None, 'unknown_surface'


def patch_result(pick_id: str, grade: str, actual: Optional[float],
                 note: Optional[str], dry_run: bool = False) -> bool:
    if dry_run:
        return True
    payload = {
        'result': grade,
        'resolved_at': datetime.now(timezone.utc).isoformat(),
    }
    if actual is not None:
        payload['actual_value'] = actual
    if note:
        payload['resolver_note'] = note
    # 2026-08-28: 3-retry with backoff. Previously a single Supabase read
    # timeout mid-regrade would raise and kill the whole 4k-row job,
    # leaving thousands of stale rows untouched. Now one blip logs +
    # skips the row; the job keeps going.
    for attempt in range(3):
        try:
            r = requests.patch(
                f'{SB}/rest/v1/external_picks?id=eq.{pick_id}',
                headers=H_WRITE, json=payload, timeout=30,
            )
            if r.status_code in (200, 201, 204):
                return True
        except requests.exceptions.RequestException:
            if attempt == 2:
                return False
            time.sleep(2 ** attempt)
    return False


def run(sport: str = 'MLB', days: Optional[int] = None,
        force_regrade: bool = False, dry_run: bool = False) -> None:
    if sport not in SPORT_CONFIG:
        print(f'  ✗ unsupported sport: {sport}')
        return

    print(f'=== external picks resolver · {sport} ===')
    if days:
        print(f'  window: last {days} days')

    picks = fetch_ungraded_picks(sport, days, force_regrade)
    print(f'  ungraded picks: {len(picks)}')
    if not picks:
        return

    game_ids = list({p['game_id'] for p in picks})
    result_map = fetch_result_map(sport, game_ids)
    print(f'  result rows fetched: {len(result_map)} '
          f'(of {len(game_ids)} referenced game_ids)')

    cfg = SPORT_CONFIG[sport]
    tally = {'W': 0, 'L': 0, 'P': 0, 'pending': 0, 'failed': 0}
    by_source = {}
    for p in picks:
        result = result_map.get(p['game_id'])
        grade, actual, note = grade_pick(p, result, cfg)
        if grade is None:
            tally['pending'] += 1
            # Still stamp resolver_note so we can see what's stuck
            if note and not dry_run:
                patch_result(p['id'], grade='', actual=None,
                             note=note, dry_run=True)  # no-op patch for now
            continue
        tally[grade] += 1
        src = p['source']
        by_source.setdefault(src, {'W': 0, 'L': 0, 'P': 0})
        by_source[src][grade] += 1
        if dry_run:
            continue
        ok = patch_result(p['id'], grade, actual, note)
        if not ok:
            tally['failed'] += 1

    total_graded = tally['W'] + tally['L'] + tally['P']
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f"\n{prefix}Overall: {tally['W']}-{tally['L']}-{tally['P']} "
          f"(n={total_graded}) · pending={tally['pending']} · failed={tally['failed']}")

    if total_graded and by_source:
        print(f'\n  Per-source hit rate:')
        for src in sorted(by_source.keys()):
            wl = by_source[src]
            n = wl['W'] + wl['L']
            if n == 0: continue
            pct = round(100 * wl['W'] / n, 1)
            print(f"    {src:14} {wl['W']}-{wl['L']}-{wl['P']} = {pct:>5.1f}% (n={n})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB', choices=list(SPORT_CONFIG.keys()))
    ap.add_argument('--days', type=int, default=None,
                    help='Only grade picks from the last N days')
    ap.add_argument('--force-regrade', action='store_true',
                    help='Re-grade rows already resolved')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(sport=args.sport, days=args.days,
        force_regrade=args.force_regrade, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
