"""One-shot backfill: correct jerry_reads.game_date for NCAAF rows whose
game_date doesn't match the date embedded in game_id.

Root cause: generate_ncaaf_game_reads.py used today_et() as game_date
when writing jerry_reads. When run on a weekday for a Sat slate, rows
landed with game_date=weekday. Grader looks up by actual game date,
never finds them.

Fix shipped in generate_ncaaf_game_reads.py (2026-09-08). This script
retroactively corrects existing bad rows so they can grade.

Usage:
    python _ncaaf_jerry_date_backfill_2026_09_08.py --dry-run
    python _ncaaf_jerry_date_backfill_2026_09_08.py             # apply
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def parse_date_from_gid(gid: str) -> str | None:
    """Extract YYYY-MM-DD from ncaaf_YYYYMMDD_... game_id."""
    if not gid or not gid.startswith('ncaaf_'): return None
    if len(gid) < 14: return None
    ymd = gid[6:14]
    if not ymd.isdigit(): return None
    return f'{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=5000)
    args = ap.parse_args()

    # Pull all NCAAF jerry_reads
    r = requests.get(f'{SB}/rest/v1/jerry_reads',
                     headers=H_R,
                     params={'sport': 'eq.NCAAF',
                             'select': 'id,game_id,game_date',
                             'order': 'game_date.desc',
                             'limit': str(args.limit)},
                     timeout=60)
    if r.status_code != 200:
        print(f'FETCH FAIL: {r.status_code} {r.text[:200]}'); return
    rows = r.json() or []
    print(f'=== NCAAF jerry_reads date backfill · {len(rows)} rows · dry={args.dry_run} ===')

    mismatches = []
    for row in rows:
        gid = row.get('game_id')
        stored_date = row.get('game_date')
        actual_date = parse_date_from_gid(gid)
        if not actual_date: continue
        if actual_date != stored_date:
            mismatches.append({'id': row['id'], 'gid': gid,
                               'stored': stored_date, 'actual': actual_date})

    print(f'  mismatches found: {len(mismatches)}')
    if not mismatches:
        print('  ✓ every row already matches — nothing to fix')
        return

    # Group by direction
    from collections import Counter
    dirs = Counter()
    for m in mismatches:
        dirs[f'{m["stored"]} → {m["actual"]}'] += 1
    print(f'  top 10 shift directions:')
    for k, v in sorted(dirs.items(), key=lambda x: -x[1])[:10]:
        print(f'    {v:>4d}  {k}')

    if args.dry_run:
        print(f'  [DRY] would patch {len(mismatches)} rows')
        return

    ok = failed = 0
    for i, m in enumerate(mismatches):
        r = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{m["id"]}',
                           headers=H_W,
                           json={'game_date': m['actual']},
                           timeout=15)
        if r.status_code in (200, 204):
            ok += 1
        else:
            failed += 1
            if failed <= 3:
                print(f'    ⚠ patch {m["id"]} failed {r.status_code}: {r.text[:150]}')
        if (i + 1) % 100 == 0:
            print(f'    progress: {i+1}/{len(mismatches)} · ok={ok} failed={failed}')

    print(f'\n=== done: patched {ok}, failed {failed} of {len(mismatches)} ===')


if __name__ == '__main__':
    main()
