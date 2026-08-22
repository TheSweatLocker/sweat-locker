"""Grade prop_pick_snapshots against mlb_pipeline_props.result.

Runs after grade_props.py (which grades the live props table). Copies the
result + computes unit_pnl using the snapshotted odds — so historical PnL
reflects what the bettor actually faced at card lock, not whatever odds
happened to be sitting on the source row when grading fired.

Sizing convention (matches Sharp Card): PRIME/STRONG = 2u, LEAN = 1u.
Push = 0. NoAction = 0.

Idempotent: only patches rows where result IS NULL. Safe to re-run.

CLI:
  python grade_pick_snapshots.py                     # yesterday
  python grade_pick_snapshots.py --date 2026-08-17
  python grade_pick_snapshots.py --days 7            # backfill last 7 days
  python grade_pick_snapshots.py --dry-run
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _american_to_win_return(odds) -> float:
    """+150 W = +1.5, -150 W = +0.67. Loss = -1.0."""
    if odds is None: return 100/110  # -110 fallback (documented accountability gap)
    o = float(odds)
    return o/100 if o >= 0 else 100/abs(o)


def _unit_pnl(tier: str, odds, result: str) -> float | None:
    if result not in ('Win', 'Loss', 'Push'): return None
    if result == 'Push': return 0.0
    stake = 2.0 if tier in ('PRIME', 'STRONG') else 1.0
    if result == 'Win':
        return round(stake * _american_to_win_return(odds), 3)
    return round(-stake, 3)


def grade_date(gd: str, dry_run: bool = False) -> tuple[int, int]:
    """Return (graded, skipped).

    2026-08-21 FIX: fallback lookup when prop_id row is missing from
    mlb_pipeline_props (source table gets pruned intraday; 596 snapshots
    were stuck ungraded because their prop_id rows were gone). Fallback:
    match by (game_date, player_name, prop_type, prop_line, direction)
    which any equivalent row will satisfy.
    """
    # Pull snapshots that need grading + FULL identifying fields for fallback
    snaps = requests.get(
        f'{SB}/rest/v1/prop_pick_snapshots',
        headers=H_READ,
        params={
            'game_date': f'eq.{gd}',
            'result': 'is.null',
            'select': 'id,prop_id,legacy_tier,direction,book_over_odds,book_under_odds,player_name,prop_type,prop_line',
        },
        timeout=30,
    ).json()
    if not snaps:
        return 0, 0
    prop_ids = {s['prop_id'] for s in snaps if s.get('prop_id')}

    # Primary lookup: by prop_id
    prop_results = {}
    if prop_ids:
        ids_csv = ','.join(f'"{pid}"' for pid in prop_ids)
        pr = requests.get(
            f'{SB}/rest/v1/mlb_pipeline_props',
            headers=H_READ,
            params={
                'id': f'in.({ids_csv})',
                'select': 'id,result',
            },
            timeout=30,
        )
        for row in (pr.json() or []):
            prop_results[row['id']] = row.get('result')

    # 2026-08-21 FALLBACK 1: for snapshots whose prop_id lookup missed OR
    # returned null result, try matching by identifying fields against
    # mlb_pipeline_props. Handles pruned source rows.
    fallback_results = {}
    unresolved = [s for s in snaps
                  if s.get('prop_id') is None
                  or prop_results.get(s.get('prop_id')) not in ('Win', 'Loss', 'Push')]
    if unresolved:
        fb = requests.get(
            f'{SB}/rest/v1/mlb_pipeline_props',
            headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-4999'},
            params={
                'game_date': f'eq.{gd}',
                'result': 'not.is.null',
                'select': 'player_name,prop_type,prop_line,direction,result',
            },
            timeout=30,
        )
        graded_props = fb.json() if fb.status_code in (200, 206) else []
        for row in graded_props:
            if not isinstance(row, dict): continue
            key = (row.get('player_name'), row.get('prop_type'),
                   row.get('prop_line'), row.get('direction'))
            fallback_results[key] = row.get('result')

    # 2026-08-21 FALLBACK 2: prop_playbook_decisions (persistent, doesn't
    # get pruned like mlb_pipeline_props). Uses W/L single-letter format
    # so we normalize here to Win/Loss for consistency with prior grades.
    # Covers 405+ snapshots that source table couldn't grade.
    if unresolved:
        fb2 = requests.get(
            f'{SB}/rest/v1/prop_playbook_decisions',
            headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-4999'},
            params={
                'game_date': f'eq.{gd}',
                'result': 'not.is.null',
                'select': 'player_name,prop_type,prop_line,direction,result',
            },
            timeout=30,
        )
        graded_pb = fb2.json() if fb2.status_code in (200, 206) else []
        for row in graded_pb:
            if not isinstance(row, dict): continue
            key = (row.get('player_name'), row.get('prop_type'),
                   row.get('prop_line'), row.get('direction'))
            # Only fill if not already found via primary fallback
            if key in fallback_results and fallback_results[key] in ('Win', 'Loss', 'Push'):
                continue
            # Normalize W/L → Win/Loss
            raw = str(row.get('result', '')).upper()
            norm = {'W': 'Win', 'L': 'Loss', 'V': 'Push', 'P': 'Push',
                    'WIN': 'Win', 'LOSS': 'Loss', 'VOID': 'Push', 'PUSH': 'Push'}.get(raw)
            if norm:
                fallback_results[key] = norm

    graded = skipped = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for s in snaps:
        res = prop_results.get(s.get('prop_id'))
        # Fallback if primary lookup missed
        if res not in ('Win', 'Loss', 'Push'):
            fb_key = (s.get('player_name'), s.get('prop_type'),
                      s.get('prop_line'), s.get('direction'))
            res = fallback_results.get(fb_key)
        if res not in ('Win', 'Loss', 'Push'):
            skipped += 1
            continue
        odds = (s.get('book_over_odds') if s.get('direction') == 'over'
                else s.get('book_under_odds'))
        pnl = _unit_pnl(s.get('legacy_tier') or 'LEAN', odds, res)
        payload = {'result': res, 'graded_at': now_iso, 'unit_pnl': pnl}
        if dry_run:
            graded += 1
            continue
        r = requests.patch(
            f'{SB}/rest/v1/prop_pick_snapshots?id=eq.{s["id"]}',
            headers=H_WRITE, json=payload, timeout=10,
        )
        if r.status_code in (200, 204):
            graded += 1
    return graded, skipped


def run(game_date: str | None = None, days: int = 1, dry_run: bool = False):
    if game_date:
        dates = [game_date]
    else:
        dates = [(date.today() - timedelta(days=d+1)).isoformat()
                 for d in range(days)]
    total_g = total_s = 0
    for gd in dates:
        g, s = grade_date(gd, dry_run=dry_run)
        total_g += g; total_s += s
        print(f'  {gd}: graded={g} skipped={s}')
    print(f'\n  {"[DRY] " if dry_run else ""}total: graded={total_g} skipped={total_s}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: yesterday ET)')
    p.add_argument('--days', type=int, default=1, help='backfill last N days')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
