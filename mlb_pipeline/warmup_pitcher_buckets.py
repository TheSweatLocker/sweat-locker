"""Batched parallel warmup of inning bucket splits for tonight's starters (2026-08-22).

Solves the "Auto-refreshed inning buckets" timing-race that added 3-6 min per
pipeline cycle. Root cause: pitcher_stats.py runs early with the known
pitcher roster. Late-confirmed starters (rookies, callups, TBD-then-named)
were missed. game_context.py then discovered the gap and refreshed each
pitcher SERIALLY inline, blocking every game's context build.

New flow: after verify_starters.py locks tonight's rotation, this script
fetches inning buckets for any missing starters in PARALLEL (up to 6 at
once), warming the mlb_pitcher_stats cache. game_context then finds all
buckets pre-populated and skips the inline refresh entirely.

Usage:
    python warmup_pitcher_buckets.py             # today (ET)
    python warmup_pitcher_buckets.py --date 2026-08-22
    python warmup_pitcher_buckets.py --workers 4  # concurrency cap

Non-fatal: any warmup failure just leaves the pitcher for game_context's
inline refresh (existing behavior). Never blocks pipeline.
"""
from __future__ import annotations
import argparse, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def fetch_tonight_starters(game_date: str) -> list[str]:
    """Return distinct starter names for tonight (home + away)."""
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'select': 'home_pitcher,away_pitcher'},
                     timeout=15)
    rows = r.json() if r.status_code == 200 else []
    names = set()
    for row in rows:
        for k in ('home_pitcher', 'away_pitcher'):
            n = row.get(k)
            if n:
                names.add(n)
    return sorted(names)


def find_missing_buckets(names: list[str]) -> list[str]:
    """Return the subset of names whose mlb_pitcher_stats row has NULL bucket data."""
    if not names:
        return []
    # PostgREST 'in' filter with comma-separated quoted strings
    encoded = ','.join(f'"{n}"' for n in names)
    r = requests.get(f'{SB}/rest/v1/mlb_pitcher_stats',
                     headers=H_READ,
                     params={'player_name': f'in.({encoded})',
                             'season': 'eq.2026',
                             'select': 'player_name,innings_1_3_era'},
                     timeout=15)
    rows = r.json() if r.status_code == 200 else []
    have_buckets = {r['player_name'] for r in rows if r.get('innings_1_3_era') is not None}
    known_rows = {r['player_name'] for r in rows}
    # Missing = names not in mlb_pitcher_stats at all OR present but buckets NULL
    missing = [n for n in names if n not in have_buckets]
    return missing


def refresh_one(name: str) -> tuple[str, bool, str]:
    """Fetch buckets + PATCH one pitcher. Returns (name, success, note)."""
    try:
        from pitcher_stats import get_inning_bucket_splits
        buckets = get_inning_bucket_splits(name)
        if not buckets:
            return (name, False, 'no split data returned')
        encoded = requests.utils.quote(name)
        r = requests.patch(
            f'{SB}/rest/v1/mlb_pitcher_stats?player_name=eq.{encoded}&season=eq.2026',
            headers=H_WRITE, json=buckets, timeout=30)
        if r.status_code in (200, 204):
            return (name, True, 'refreshed')
        return (name, False, f'PATCH {r.status_code}')
    except Exception as e:
        return (name, False, f'exception: {e}')


def main(game_date: str | None = None, workers: int = 6) -> None:
    gd = game_date or _et_today()
    print(f'=== warmup_pitcher_buckets · {gd} ===')
    starters = fetch_tonight_starters(gd)
    if not starters:
        print('  no starters found — skipping')
        return
    print(f'  tonight starters: {len(starters)}')
    missing = find_missing_buckets(starters)
    if not missing:
        print(f'  ✅ all {len(starters)} starters already have bucket data — no warmup needed')
        return
    print(f'  warming {len(missing)} pitchers with {workers} parallel workers')
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(refresh_one, n): n for n in missing}
        for fut in as_completed(futures):
            name, success, note = fut.result()
            if success:
                ok += 1; print(f'  ✓ {name}: {note}')
            else:
                fail += 1; print(f'  ✗ {name}: {note}')
    print(f'  done: {ok} refreshed, {fail} failed (game_context will retry inline if needed)')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--workers', type=int, default=6)
    args = p.parse_args()
    main(game_date=args.date, workers=args.workers)
