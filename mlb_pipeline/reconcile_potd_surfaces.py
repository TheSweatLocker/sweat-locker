"""reconcile_potd_surfaces — end-of-pipeline POTD source-of-truth sync.

Root problem: jerry_cache.best_bet_{today} has 3 writers
(play_of_day, play_of_day_multi_sport, jerry_anchor_potd) plus a
narrative writer (generate_potd_narrative). Only jerry_anchor_potd
mirrors to daily_best_bet_history. Other writes leave history stale
→ home page + Receipts + calendar disagree on today's POTD.

This script runs LAST in the pipeline (after every POTD writer has
had a chance to fire). It reads jerry_cache as the AUTHORITY and
force-writes daily_best_bet_history to match:
  - Real pick    → history.lean = leanDisplay, result stays what it was
  - noPlay       → history.lean = 'No Play (discipline pass)', result = 'No Play'
  - noGames      → history.lean = 'No Games', result = 'No Play'

Idempotent. Safe to re-run. READS jerry_cache, WRITES history only.

CLI:
  python reconcile_potd_surfaces.py           # today
  python reconcile_potd_surfaces.py --date 2026-09-08
  python reconcile_potd_surfaces.py --dry-run
"""
from __future__ import annotations
import argparse, json, os, sys
import datetime as dt
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

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _et_today() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date().isoformat()


def reconcile(date: str, dry_run: bool = False) -> int:
    """Return 0 if converged, 1 if no jerry_cache row (nothing to do),
    2 if history patch failed."""
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_R,
        params={'cache_key': f'eq.best_bet_{date}',
                'select': 'data,narrative,fetched_at'},
        timeout=10,
    )
    if r.status_code != 200 or not r.json():
        print(f'  no jerry_cache best_bet for {date} — nothing to reconcile')
        return 1
    cache = r.json()[0]
    data = cache.get('data') or {}
    if isinstance(data, str):
        try: data = json.loads(data)
        except Exception: data = {}

    # Determine target history state from cache
    if data.get('noPlay'):
        target_lean = 'No Play (discipline pass)'
        target_result = 'No Play'
        state_label = 'noPlay'
    elif data.get('noGames'):
        target_lean = 'No Games'
        target_result = 'No Play'
        state_label = 'noGames'
    elif data.get('game') or data.get('leanDisplay'):
        game = data.get('game') or {}
        target_lean = data.get('leanDisplay') or (
            f"{game.get('away_team','?')} @ {game.get('home_team','?')}")
        # DO NOT overwrite result — grader owns that
        target_result = None
        state_label = f'pick: {target_lean[:50]}'
    else:
        print(f'  {date}: jerry_cache has no recognizable state — skip')
        return 1

    # Check current history state
    hr = requests.get(
        f'{SB}/rest/v1/daily_best_bet_history',
        headers=H_R,
        params={'bet_date': f'eq.{date}',
                'select': 'lean,result', 'limit': '1'},
        timeout=10,
    )
    hist_rows = hr.json() if hr.status_code == 200 else []
    if not hist_rows:
        print(f'  {date}: no history row exists — cache-only publish, skipping')
        return 1

    hist = hist_rows[0]
    hist_lean = hist.get('lean') or ''
    hist_result = hist.get('result') or ''

    # Compute diff
    needs_update = False
    patch = {}
    if target_lean and hist_lean != target_lean:
        patch['lean'] = target_lean
        needs_update = True
    if target_result is not None and hist_result != target_result:
        patch['result'] = target_result
        needs_update = True

    if not needs_update:
        print(f'  {date}: converged ({state_label}) — no change')
        return 0

    if dry_run:
        print(f'  [DRY] {date}: would patch history {patch}')
        print(f'         current: lean={hist_lean!r}, result={hist_result!r}')
        return 0

    pr = requests.patch(
        f'{SB}/rest/v1/daily_best_bet_history',
        headers=H_W,
        params={'bet_date': f'eq.{date}'},
        json=patch,
        timeout=15,
    )
    if pr.status_code in (200, 204):
        print(f'  ✓ {date}: reconciled ({state_label})')
        print(f'    patched: {patch}')
        return 0
    print(f'  ✗ {date}: patch failed {pr.status_code}: {pr.text[:150]}')
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    date = args.date or _et_today()
    print(f'=== reconcile_potd_surfaces · {date} · '
          f'{"DRY" if args.dry_run else "APPLY"} ===')
    sys.exit(reconcile(date, dry_run=args.dry_run))


if __name__ == '__main__':
    main()
