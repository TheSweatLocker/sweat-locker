"""One-shot backfill of public_splits_archive from historical line_snapshot data.

Runs against ALL existing OddsCrowd snapshots (line_snapshot table) and
writes them into the permanent archive with their ORIGINAL snapshot_ts.
Fadereport is joined best-effort where fadereport_signals rows match.

Idempotent — unique index on (sport, game_id, market, pick_side, captured_at)
means repeat runs overwrite in place.

CLI
  python backfill_public_splits_archive.py           # all history, all sports
  python backfill_public_splits_archive.py --days 90 # last 90 days only
"""
from __future__ import annotations
import argparse, os, sys
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
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


def pull_oc(days: int, page: int = 0, page_size: int = 1000) -> list:
    """Paginated OC snapshot pull (1000/page — PostgREST default max)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace('+', '%2B')
    offset = page * page_size
    r = requests.get(
        f'{SB}/rest/v1/line_snapshot'
        f'?source=eq.oddscrowd&snapshot_ts=gte.{since}'
        f'&select=sport,game_id,market,pick_side,money_pct,bets_pct,divergence,line,snapshot_ts'
        f'&order=snapshot_ts.asc&limit={page_size}&offset={offset}',
        headers=H_READ, timeout=60)
    if r.status_code != 200:
        print(f'  ✗ OC page {page}: {r.status_code}')
        return []
    return r.json() or []


def pull_fr(days: int) -> dict:
    """Return {(sport, game_id, market, pick_side): [rows_by_ts_asc]}."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace('+', '%2B')
    idx = {}
    for page in range(20):  # up to 100k
        r = requests.get(
            f'{SB}/rest/v1/fadereport_signals'
            f'?captured_at=gte.{since}'
            f'&select=sport,game_id,market,pick_side,handle_pct,bettors_pct,captured_at'
            f'&order=captured_at.asc',
            headers={**H_READ, 'Range-Unit': 'items',
                     'Range': f'{page*5000}-{(page+1)*5000-1}'}, timeout=30)
        if r.status_code not in (200, 206):
            break
        rows = r.json() or []
        for row in rows:
            key = (row.get('sport'), row.get('game_id'),
                   (row.get('market') or '').lower(),
                   (row.get('pick_side') or '').upper())
            idx.setdefault(key, []).append(row)
        if len(rows) < 5000:
            break
    return idx


def nearest_fr(fr_bucket: list, target_ts: str):
    """Find the fadereport row whose captured_at is closest to target_ts."""
    if not fr_bucket: return None
    try:
        target = datetime.fromisoformat(target_ts.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None
    best = None; best_delta = None
    for row in fr_bucket:
        try:
            ts = datetime.fromisoformat(row['captured_at'].replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            continue
        delta = abs((ts - target).total_seconds())
        if delta > 24 * 3600:  # only within 24hr
            continue
        if best_delta is None or delta < best_delta:
            best = row; best_delta = delta
    return best


def run(days: int, dry_run: bool = False) -> int:
    print(f'=== Backfilling public_splits_archive · last {days}d ===')
    fr_idx = pull_fr(days)
    print(f'  Fadereport index: {sum(len(v) for v in fr_idx.values())} rows')

    total_written = 0
    for page in range(500):  # up to 500k OC rows
        oc_rows = pull_oc(days, page=page)
        if not oc_rows: break
        payload = []
        for row in oc_rows:
            sport = row.get('sport')
            gid   = row.get('game_id')
            mkt   = (row.get('market') or '').lower()
            side  = (row.get('pick_side') or '').upper()
            if not sport or not gid or not mkt or not side: continue
            fr = nearest_fr(fr_idx.get((sport, gid, mkt, side), []), row['snapshot_ts'])
            payload.append({
                'sport':          sport,
                'game_id':        gid,
                'market':         mkt,
                'pick_side':      side,
                'oc_money_pct':   row.get('money_pct'),
                'oc_bets_pct':    row.get('bets_pct'),
                'oc_divergence':  row.get('divergence'),
                'fr_handle_pct':  (fr or {}).get('handle_pct'),
                'fr_bettors_pct': (fr or {}).get('bettors_pct'),
                'current_line':   row.get('line'),
                'captured_at':    row['snapshot_ts'],
            })
        if dry_run:
            print(f'  [DRY] page {page}: {len(payload)} rows (first: {payload[0] if payload else None})')
            total_written += len(payload)
            if len(oc_rows) < 1000: break
            continue
        # Write in chunks of 500
        for i in range(0, len(payload), 500):
            chunk = payload[i:i+500]
            r = requests.post(
                f'{SB}/rest/v1/public_splits_archive'
                f'?on_conflict=sport,game_id,market,pick_side,captured_at',
                headers=H_WRITE, json=chunk, timeout=60)
            if r.status_code in (200, 201, 204):
                total_written += len(chunk)
            else:
                print(f'  ✗ page {page} chunk {i}: {r.status_code} {r.text[:200]}')
        if page % 10 == 0:
            print(f'  page {page}: {len(payload)} rows written (running total: {total_written})')
        if len(oc_rows) < 1000: break

    print(f'\n  ✓ Backfill complete: {total_written} archive rows written')
    return total_written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=365)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
