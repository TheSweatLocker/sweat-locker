"""Ledger snapshot recorder (2026-08-18).

Runs AFTER generate_ledger.py in the cron chain. Reads today's
ledger_suggestions rows and writes an IMMUTABLE snapshot to
ledger_snapshots — locking the odds/legs at generation time so
historical PnL reflects what the user actually saw when they placed
the combo (not whatever the odds moved to before grading).

Idempotent: uses unique (game_date, kind, sport_scope, combined_odds).

Companion to prop_pick_snapshots — same transparency pattern user
approved earlier today.
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timezone, timedelta
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
           'Prefer': 'resolution=ignore-duplicates,return=minimal'}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def run(game_date: str = None, dry_run: bool = False):
    gd = game_date or _et_today()
    print(f'=== snapshot_ledger · {gd}{" [DRY]" if dry_run else ""} ===')

    r = requests.get(
        f'{SB}/rest/v1/ledger_suggestions',
        headers=H_READ,
        params={'game_date': f'eq.{gd}',
                'select': 'id,kind,sport_scope,legs,combined_odds,reasoning'},
        timeout=15,
    )
    suggs = r.json() if r.status_code == 200 else []
    if not suggs:
        print('  no ledger suggestions today — nothing to snapshot')
        return
    print(f'  {len(suggs)} suggestions to snapshot')

    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = [{
        'game_date': gd,
        'snapshotted_at': now_iso,
        'ledger_suggestion_id': s['id'],
        'kind': s['kind'],
        'sport_scope': s['sport_scope'],
        'legs': s['legs'],  # immutable copy of legs at this moment
        'combined_odds': s['combined_odds'],
        'reasoning': s.get('reasoning'),
    } for s in suggs]

    if dry_run:
        for p in payloads:
            print(f"  [DRY] {p['kind']:<24} {p['sport_scope']:<6} "
                  f"combined={p['combined_odds']:+d} legs={len(p['legs'])}")
        return

    written = 0
    for p in payloads:
        pr = requests.post(
            f'{SB}/rest/v1/ledger_snapshots'
            f'?on_conflict=game_date,kind,sport_scope,combined_odds',
            headers=H_WRITE, json=[p], timeout=15,
        )
        if pr.status_code in (200, 201, 204):
            written += 1
        else:
            print(f'  x snapshot failed: {pr.status_code} {pr.text[:150]}')
    print(f'  ✓ upserted {written} ledger snapshots')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
