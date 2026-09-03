"""One-off NCAAF dedupe for the 30 dupe game_ids left over from ingest
BEFORE the canonical-short-form fix in ncaaf_odds_pull.py shipped 9/3.

Deletes the mascot-suffixed variant (e.g. 'Merrimack Warriors_Delaware
Blue Hens') from ncaaf_game_context + jerry_reads. Leaves
primary_play_snapshots alone (audit journal — orphaned rows fine).

Safe to run: only touches game_ids in _ncaaf_dedupe_plan.txt.
Idempotent: 404s on already-deleted rows are ignored.

Usage:
  python _ncaaf_dedupe_2026_09_03.py --dry-run   # preview
  python _ncaaf_dedupe_2026_09_03.py             # execute
"""
import argparse, os, sys
from pathlib import Path
from urllib.parse import quote

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
K = os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': f'Bearer {K}', 'Prefer': 'return=minimal'}


def main(dry_run: bool):
    plan_path = Path(__file__).parent / '_ncaaf_dedupe_plan.txt'
    if not plan_path.exists():
        print('missing _ncaaf_dedupe_plan.txt; rebuild via inline dedup plan script')
        return 1
    plan = plan_path.read_text()
    deletes = [line for line in plan.split('DELETE\n')[1].split('\n\n')[0].split('\n') if line]
    print(f'Will delete {len(deletes)} dupe game_ids')

    if dry_run:
        for gid in deletes[:10]:
            print(f'  [DRY] {gid}')
        if len(deletes) > 10:
            print(f'  [DRY] ... + {len(deletes)-10} more')
        return 0

    ctx_del = jerry_del = 0
    errors = []
    for gid in deletes:
        gid_enc = quote(gid, safe='')
        r = requests.delete(f'{SB}/rest/v1/jerry_reads?game_id=eq.{gid_enc}',
                            headers=H, timeout=15)
        if r.status_code in (200, 204): jerry_del += 1
        else: errors.append(('jerry_reads', gid, r.status_code, r.text[:100]))
        r = requests.delete(f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{gid_enc}',
                            headers=H, timeout=15)
        if r.status_code in (200, 204): ctx_del += 1
        else: errors.append(('ncaaf_game_context', gid, r.status_code, r.text[:100]))

    print(f'  ncaaf_game_context deletes: {ctx_del}/{len(deletes)}')
    print(f'  jerry_reads deletes: {jerry_del}/{len(deletes)}')
    if errors:
        print('Errors (first 5):')
        for e in errors[:5]: print(f'  {e}')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    sys.exit(main(dry_run=ap.parse_args().dry_run))
