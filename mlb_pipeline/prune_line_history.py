"""prune_line_history — TTL cleanup for line_history table.

Root problem (2026-09-08 audit): line_history was 62.5% of the entire
Supabase DB — ~2M rows, growing daily. Consumers (detect_line_movement,
LineMovementTab) only look back HOURS (max 24-48h lookback), so anything
older than a few weeks is dead weight.

Retention default: 14 days (conservative — covers rescue capacity +
weekend catchup + backtest windows).

Deletes in batches of 5000 to avoid statement timeout on huge single
DELETEs. Dry-run by default; --apply to actually delete.

CLI:
  python prune_line_history.py                # dry-run · 14d default
  python prune_line_history.py --days 7       # dry-run · 7d
  python prune_line_history.py --apply        # actually delete
"""
from __future__ import annotations
import argparse
import os
import sys
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

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _count_older_than(cutoff_iso: str) -> int | None:
    """Return count of rows older than cutoff via HEAD + count=exact."""
    r = requests.head(
        f'{SB}/rest/v1/line_history',
        headers={**H_READ, 'Prefer': 'count=exact'},
        params={'captured_at': f'lt.{cutoff_iso}'},
        timeout=15,
    )
    cr = r.headers.get('content-range', '')
    if '/' in cr:
        try:
            return int(cr.split('/')[-1])
        except (ValueError, IndexError):
            return None
    return None


def prune(days: int, apply: bool, batch_size: int = 5000) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    total_older = _count_older_than(cutoff)
    print(f'== prune_line_history · cutoff = {cutoff} ({days}d ago) ==')
    if total_older is None:
        print('  ⚠ could not count rows — bailing out for safety')
        return 1
    print(f'  rows older than cutoff: {total_older:,}')
    if total_older == 0:
        print('  nothing to prune.')
        return 0

    if not apply:
        print(f'  [DRY-RUN] would delete {total_older:,} rows in batches of {batch_size}')
        print(f'  re-run with --apply to actually delete.')
        return 0

    deleted = 0
    while True:
        # PostgREST DELETE with `?captured_at=lt.X&limit=` isn't supported;
        # workaround: fetch a batch of ids, then DELETE by id IN (...)
        r = requests.get(
            f'{SB}/rest/v1/line_history',
            headers=H_READ,
            params={'captured_at': f'lt.{cutoff}',
                    'select': 'id',
                    'limit': batch_size},
            timeout=30,
        )
        if r.status_code != 200:
            print(f'  ✗ fetch batch failed {r.status_code}: {r.text[:200]}')
            return 1
        ids = [row['id'] for row in r.json()]
        if not ids:
            break
        # Build IN(...) filter — PostgREST syntax: id=in.(1,2,3)
        id_list = ','.join(str(i) for i in ids)
        dr = requests.delete(
            f'{SB}/rest/v1/line_history',
            headers=H_WRITE,
            params={'id': f'in.({id_list})'},
            timeout=60,
        )
        if dr.status_code in (200, 204):
            deleted += len(ids)
            print(f'  ✓ deleted batch of {len(ids)} ({deleted:,}/{total_older:,} total)')
        else:
            print(f'  ✗ delete failed {dr.status_code}: {dr.text[:200]}')
            return 1

    print(f'\n✓ pruned {deleted:,} rows older than {days}d')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=14,
                    help='Delete rows older than this many days (default 14)')
    ap.add_argument('--apply', action='store_true',
                    help='Actually delete. Without this flag, dry-run only.')
    ap.add_argument('--batch-size', type=int, default=5000)
    args = ap.parse_args()
    sys.exit(prune(days=args.days, apply=args.apply, batch_size=args.batch_size))


if __name__ == '__main__':
    main()
