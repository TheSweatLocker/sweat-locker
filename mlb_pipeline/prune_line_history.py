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
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except ImportError:
    Retry = None

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


# 2026-09-21: this walked ~354 sequential fetch+DELETE batches, each on a
# fresh socket, and the host reset the connection partway through — it
# deleted 267,194 of 1,771,412 target rows and then EXITED 0, so it looked
# like a clean run. Fourth script today with the same defect
# (grade_public_receipts, the classify_line_moves backfill, and
# backfill_nhl_grades were the others). Pool the connections, retry
# transient failures, and report an incomplete run as incomplete.
_S = requests.Session()
if Retry is not None:
    _S.mount('https://', HTTPAdapter(
        max_retries=Retry(total=5, backoff_factor=0.5,
                          status_forcelist=(500, 502, 503, 504, 429),
                          allowed_methods=frozenset(['GET', 'DELETE'])),
        pool_connections=8, pool_maxsize=8))


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
        r = _S.get(
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
        dr = _S.delete(
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

    # 2026-09-21: say so when the run did not finish. The previous version
    # printed a ✓ and returned 0 after a connection reset left 1.5M of
    # 1.77M target rows in place — indistinguishable from success, and the
    # only reason it was caught was checking the row count afterwards.
    if deleted < total_older:
        print(f'\n⚠ pruned {deleted:,} of {total_older:,} rows older than {days}d '
              f'— INCOMPLETE, {total_older - deleted:,} remain. Re-run to finish.')
        return 2
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
