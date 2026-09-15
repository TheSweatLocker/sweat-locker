"""refresh_missing_sweat_scores.py — chain-fire sweat score + jerry_synth for late-added games.

2026-09-15 · closes the "late slate arrival" gap.

Context: mlb_game_context rows can be added by the ingest pipeline any time
during the morning (e.g. 9:32 AM ET today for DET@TOR + BOS@TEX + SD@COL).
The main pipeline runs at 6 AM / 7:15 AM / 8:30 AM ET + 2 PM ET — a game
added at 9:32 AM misses every morning wave and stays dark until 2 PM.

play_of_day.py's sweat-score loop is a prereq for generate_jerry_synthesis
(which skips games with NULL sweat_score). Users see missing reads on late-
added games for hours.

This script is a targeted sweep meant to run every hour between the 8:30 AM
main and the 2 PM main:

  1. Query mlb_game_context for TODAY where sweat_score IS NULL
  2. If any missing, invoke play_of_day.py (writes sweat_scores across all
     of today's games — cheap, idempotent, respects POTD lock)
  3. Invoke generate_jerry_synthesis.py (writes jerry_reads for
     newly-scored games; skips games that already have reads)
  4. Optionally trigger reconcile_potd_surfaces to keep POTD in sync

Non-fatal on individual step failures — logs and continues.

USAGE:
    python refresh_missing_sweat_scores.py                    # today ET
    python refresh_missing_sweat_scores.py --date 2026-09-15  # explicit
    python refresh_missing_sweat_scores.py --dry-run          # log only
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

for _env in (Path(__file__).parent / '.env', Path(__file__).parent.parent / '.env'):
    if _env.exists():
        for line in _env.read_text().split('\n'):
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def count_missing_sweat_scores(game_date: str) -> tuple[int, list[dict]]:
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context',
        params={'game_date': f'eq.{game_date}',
                'select': 'away_team,home_team,sweat_score',
                'sweat_score': 'is.null'},
        headers=H, timeout=30,
    )
    if r.status_code != 200: return (0, [])
    rows = [x for x in (r.json() or []) if isinstance(x, dict)]
    return (len(rows), rows)


def count_missing_jerry_reads(game_date: str) -> tuple[int, list[str]]:
    r_ctx = requests.get(f'{SB}/rest/v1/mlb_game_context',
        params={'game_date': f'eq.{game_date}', 'select': 'game_id'},
        headers=H, timeout=30)
    r_jr = requests.get(f'{SB}/rest/v1/jerry_reads',
        params={'sport': 'eq.MLB', 'game_date': f'eq.{game_date}', 'select': 'game_id'},
        headers=H, timeout=30)
    if r_ctx.status_code != 200 or r_jr.status_code != 200: return (0, [])
    ctx_ids = {x['game_id'] for x in r_ctx.json() if isinstance(x, dict)}
    jr_ids = {x['game_id'] for x in r_jr.json() if isinstance(x, dict)}
    missing = list(ctx_ids - jr_ids)
    return (len(missing), missing)


def _run(cmd_args: list, label: str, dry_run: bool) -> int:
    print(f'\n== {label} ==')
    if dry_run:
        print(f'  [DRY] would run: {" ".join(cmd_args)}')
        return 0
    # Force UTF-8 so emoji-heavy print() doesn't crash on Windows CI
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    try:
        proc = subprocess.run(cmd_args, env=env, timeout=300,
                              capture_output=True, text=True)
        for line in (proc.stdout or '').splitlines()[-20:]:
            print(f'  {line}')
        if proc.returncode != 0:
            print(f'  ⚠ {label} exit={proc.returncode} — non-fatal, continuing')
            for line in (proc.stderr or '').splitlines()[-5:]:
                print(f'    err: {line}')
        return proc.returncode
    except subprocess.TimeoutExpired:
        print(f'  ⚠ {label} timed out — non-fatal')
        return 1
    except Exception as e:
        print(f'  ⚠ {label} failed: {e} — non-fatal')
        return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='Target date (default: today ET)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    gd = args.date or _today_et()
    print(f'=== refresh_missing_sweat_scores · {gd} ===')

    n_miss_sweat, miss_sweat_rows = count_missing_sweat_scores(gd)
    n_miss_reads, _ = count_missing_jerry_reads(gd)

    print(f'  missing sweat_score: {n_miss_sweat}')
    for r in miss_sweat_rows[:5]:
        print(f'    · {r.get("away_team")}@{r.get("home_team")}')
    print(f'  missing jerry_reads: {n_miss_reads}')

    if n_miss_sweat == 0 and n_miss_reads == 0:
        print('  ✓ nothing to do — full coverage')
        return

    script_dir = Path(__file__).parent

    if n_miss_sweat > 0:
        _run([sys.executable, str(script_dir / 'play_of_day.py')],
             'play_of_day.py (sweat_score refresh)', args.dry_run)

    if n_miss_reads > 0 or n_miss_sweat > 0:
        _run([sys.executable, str(script_dir / 'generate_jerry_synthesis.py')],
             'generate_jerry_synthesis.py (fill missing reads)', args.dry_run)
        # Reconcile POTD surfaces so best_bet_{gd} + daily_best_bet_history match.
        _reconcile = script_dir / 'reconcile_potd_surfaces.py'
        if _reconcile.exists():
            _run([sys.executable, str(_reconcile)],
                 'reconcile_potd_surfaces.py', args.dry_run)

    # Post-run verification
    n_after_sweat, _ = count_missing_sweat_scores(gd)
    n_after_reads, _ = count_missing_jerry_reads(gd)
    print(f'\n=== POST-RUN ===')
    print(f'  missing sweat_score: {n_miss_sweat} → {n_after_sweat}')
    print(f'  missing jerry_reads: {n_miss_reads} → {n_after_reads}')
    if n_after_sweat > 0 or n_after_reads > 0:
        print(f'  ⚠ residual gaps — likely TBD-pitcher games (legit block)')


if __name__ == '__main__':
    main()
