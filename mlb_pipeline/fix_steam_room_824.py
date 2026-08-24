"""One-shot Steam Room repair — run when Supabase is healthy.

Fixes 2 CRITICAL bugs surfaced by 8/24 E2E audit:

  1. Wrong-side ladder rung — id=9 (Springs Under 3.5 ER) was written
     before today's direction-gate fix. Users on Ladder tab are on the
     wrong side of the compounding play. This clears it and re-runs the
     qualifier with the fixed code.

  2. Line Movement pipeline dead — 0 writes to line_history in 4+ days.
     Root cause hypothesis: odds_cache 'odds_games_MLB_*' rows are stale
     or absent. This diagnoses first, then runs write_line_history if
     the cache is healthy.

USAGE
─────
    python _fix_steam_room_824.py --dry-run    # diagnose only
    python _fix_steam_room_824.py              # apply fixes

Idempotent — safe to re-run.
"""
from __future__ import annotations
import argparse, os, sys, json, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


# ─── FIX 1: LADDER RUNG ────────────────────────────────────────
def diagnose_ladder():
    print('\n=== LADDER DIAGNOSIS ===', flush=True)
    r = requests.get(f'{SB}/rest/v1/ladder_state?select=*&order=updated_at.desc&limit=1',
                     headers=H_READ, timeout=15)
    state = r.json()[0] if r.status_code == 200 and r.json() else None
    print(f'  active_rung_id: {state.get("active_rung_id") if state else None}')
    print(f'  streak:         {state.get("streak") if state else None}')

    # Today's rungs
    today = et_today()
    r2 = requests.get(f'{SB}/rest/v1/ladder_rung?game_date=eq.{today}&select=*',
                      headers=H_READ, timeout=15)
    rungs = r2.json() if r2.status_code == 200 else []
    print(f'  rungs for {today}: {len(rungs)}')
    for rg in rungs:
        print(f'    id={rg.get("id")} label={rg.get("pick_label","")[:60]} '
              f'tier={rg.get("tier")} result={rg.get("result")}')
    return state, rungs


def fix_ladder(dry_run=False):
    state, rungs = diagnose_ladder()
    if not rungs:
        print('  no rungs today — nothing to clear'); return

    # Clear ALL today's rungs so the re-run has a clean slate. The relaxed-
    # scan fallback handles the "no qualifier" case; user gets an honest
    # "no ladder today" instead of a wrong-side stale pick.
    for rg in rungs:
        rid = rg.get('id')
        if dry_run:
            print(f'  [DRY] would delete rung id={rid}')
            continue
        r = requests.delete(f'{SB}/rest/v1/ladder_rung?id=eq.{rid}',
                           headers=H_WRITE, timeout=10)
        print(f'  deleted rung id={rid}: status={r.status_code}')

    # Clear active_rung_id on state (leave streak intact)
    if state and state.get('active_rung_id'):
        if dry_run:
            print('  [DRY] would clear ladder_state.active_rung_id')
        else:
            r = requests.patch(f'{SB}/rest/v1/ladder_state?id=eq.{state["id"]}',
                              headers=H_WRITE,
                              json={'active_rung_id': None},
                              timeout=10)
            print(f'  cleared active_rung_id: status={r.status_code}')

    if dry_run:
        return

    # Re-run steam_room_ladder — this time the direction gate (b09e2e1e)
    # blocks FADE-side qualifiers. If no BACK qualifier fires, relaxed scan
    # picks next-best; if THAT fails, no rung today (correct outcome).
    print('  re-running steam_room_ladder.py...')
    result = subprocess.run(
        ['python', str(Path(__file__).parent / 'steam_room_ladder.py')],
        capture_output=True, text=True, timeout=180,
    )
    print(f'    exit code: {result.returncode}')
    if result.stdout:
        for line in result.stdout.split('\n')[-15:]:
            print(f'    | {line}')


# ─── FIX 2: LINE MOVEMENT DIAGNOSTIC ───────────────────────────
def diagnose_line_movement():
    print('\n=== LINE MOVEMENT DIAGNOSIS ===', flush=True)

    # Check odds_cache for MLB prefix
    r = requests.get(f'{SB}/rest/v1/odds_cache',
                     params={'cache_key': 'like.odds_games_MLB_%',
                             'select': 'cache_key,fetched_at',
                             'order': 'fetched_at.desc', 'limit': 5},
                     headers=H_READ, timeout=15)
    rows = r.json() if r.status_code == 200 else []
    print(f'  odds_cache MLB rows: {len(rows)}')
    for row in rows[:3]:
        print(f'    key={row.get("cache_key")} fetched={row.get("fetched_at","")[:19]}')

    # Check line_history last write
    r2 = requests.get(f'{SB}/rest/v1/line_history',
                     params={'select': 'captured_at', 'order': 'captured_at.desc', 'limit': 1},
                     headers=H_READ, timeout=15)
    lh = r2.json() if r2.status_code == 200 else []
    print(f'  line_history last write: {lh[0].get("captured_at","")[:19] if lh else "NONE"}')

    # Check mlb_line_history (poller output) — might still be writing
    r3 = requests.get(f'{SB}/rest/v1/mlb_line_history',
                     params={'select': 'captured_at', 'order': 'captured_at.desc', 'limit': 1},
                     headers=H_READ, timeout=15)
    mlh = r3.json() if r3.status_code == 200 else []
    print(f'  mlb_line_history last write: {mlh[0].get("captured_at","")[:19] if mlh else "NONE"}')

    return rows, lh, mlh


def fix_line_movement(dry_run=False):
    rows, lh, mlh = diagnose_line_movement()

    if not rows:
        print('  ⚠️ odds_cache MLB is EMPTY — root cause confirmed.')
        print('     Fix upstream: pull_odds.py or whatever writes odds_games_MLB_*')
        print('     Not a write_line_history bug — no data to snapshot.')
        return

    now = datetime.now(timezone.utc)
    if rows[0].get('fetched_at'):
        try:
            latest = datetime.fromisoformat(rows[0]['fetched_at'].replace('Z','+00:00'))
            age_hrs = (now - latest).total_seconds() / 3600
            print(f'  odds_cache freshest: {age_hrs:.1f}h old')
            if age_hrs > 12:
                print('  ⚠️ odds_cache is stale (>12h). Upstream odds puller is broken.')
        except Exception:
            pass

    if dry_run:
        print('  [DRY] would run write_line_history.py --sport ALL')
        return

    # Try to catch up line_history from whatever odds_cache still has
    print('  running write_line_history.py --sport ALL...')
    result = subprocess.run(
        ['python', str(Path(__file__).parent / 'write_line_history.py'), '--sport', 'ALL'],
        capture_output=True, text=True, timeout=180,
    )
    print(f'    exit code: {result.returncode}')
    if result.stdout:
        for line in result.stdout.split('\n')[-20:]:
            print(f'    | {line}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--only', choices=['ladder', 'line_movement'])
    args = ap.parse_args()

    if args.only in (None, 'ladder'):
        fix_ladder(dry_run=args.dry_run)
    if args.only in (None, 'line_movement'):
        fix_line_movement(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
