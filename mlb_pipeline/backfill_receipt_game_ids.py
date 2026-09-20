"""Populate public_receipts.game_id where it is missing.

game_id was added by migration 20260920a so a receipt can be joined to
its context, its result, and the other picks on the same game. Only the
prop_jerry source carried one, so 10,238 of 12,774 receipts have none and
per-game audit ("show me everything we said about this matchup") needs a
lookup chain instead of a join.

Recovery is by source_table + source_id, which every receipt has:
    prop_jerry  -> prop_jerry_reads.id      -> game_id
    game_read   -> jerry_reads.id           -> game_id
    ledger      -> ledger_snapshots has no game_id (parlays span games)
    daily_degen -> cross-sport, no single game_id

So ledger and daily_degen receipts legitimately have no game_id and are
skipped rather than guessed at. A wrong game_id is worse than none: it
would silently attach a parlay to one arbitrary leg's game.

Idempotent — only fills rows where game_id IS NULL, never overwrites.

    python backfill_receipt_game_ids.py --dry-run
    python backfill_receipt_game_ids.py --apply
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8')
_ENV = Path(__file__).parent / '.env'
for _l in _ENV.read_text().splitlines():
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

SOURCE_TABLE = {
    'prop_jerry_reads': 'prop_jerry_reads',
    'jerry_reads': 'jerry_reads',
}


def page(table: str, select: str, extra: str = '') -> list:
    out, off = [], 0
    while off < 100000:
        r = requests.get(f'{SB}/rest/v1/{table}?select={select}{extra}'
                         f'&limit=1000&offset={off}', headers=H, timeout=60)
        if r.status_code != 200:
            print(f'  ⚠ {table} fetch {r.status_code}: {r.text[:160]}')
            return out
        b = r.json()
        if not b:
            break
        out += b
        off += 1000
        if len(b) < 1000:
            break
        time.sleep(0.12)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    dry = args.dry_run

    receipts = page('public_receipts', 'id,source_table,source_id,game_id,surface',
                    '&game_id=is.null')
    print(f'receipts missing game_id: {len(receipts)}')
    print('  by source_table:',
          dict(Counter(r.get('source_table') for r in receipts)))

    # Build id -> game_id maps for the two sources that have one.
    maps: dict[str, dict] = {}
    for st in SOURCE_TABLE.values():
        rows = page(st, 'id,game_id')
        maps[st] = {str(r['id']): r.get('game_id') for r in rows
                    if r.get('game_id')}
        print(f'  {st}: {len(maps[st])} id->game_id pairs')

    fixable, skipped = [], Counter()
    for r in receipts:
        st = r.get('source_table')
        if st not in maps:
            skipped[st or 'unknown'] += 1
            continue
        gid = maps[st].get(str(r.get('source_id')))
        if not gid:
            skipped[f'{st}:no-match'] += 1
            continue
        fixable.append((r['id'], gid))

    print(f'\n  recoverable: {len(fixable)}')
    print(f'  skipped    : {dict(skipped)}')
    print('   (ledger + daily_degen span multiple games — no single '
          'game_id exists, so they are left null rather than guessed)')

    if dry:
        print('\n--apply to write.')
        return 0

    # 2026-09-20: grouped by game_id rather than one PATCH per receipt.
    # Per-row writes meant 9,678 requests and the host reset the
    # connection partway through (ConnectionResetError 10054). Receipts
    # share game_ids heavily — many props per game — so grouping collapses
    # it to a few hundred calls.
    #
    # Safe to re-run: the fetch above only selects game_id IS NULL, so a
    # resumed run simply picks up whatever is still unset.
    by_gid: dict[str, list] = {}
    for rid, gid in fixable:
        by_gid.setdefault(gid, []).append(rid)
    print(f'  grouped into {len(by_gid)} game_ids\n')

    ok = fail = 0
    for i, (gid, rids) in enumerate(by_gid.items(), 1):
        # Chunk the id list so the URL stays a sane length.
        for j in range(0, len(rids), 100):
            chunk = rids[j:j + 100]
            ids = ','.join(str(x) for x in chunk)
            for attempt in range(3):
                try:
                    w = requests.patch(
                        f'{SB}/rest/v1/public_receipts?id=in.({ids})',
                        headers=HW, json={'game_id': gid}, timeout=45)
                    if w.status_code in (200, 204):
                        ok += len(chunk)
                    else:
                        fail += len(chunk)
                        if fail <= 200:
                            print(f'  ⚠ {w.status_code}: {w.text[:140]}')
                    break
                except requests.exceptions.RequestException as e:
                    if attempt == 2:
                        fail += len(chunk)
                        print(f'  ⚠ gave up on {len(chunk)} rows: {str(e)[:90]}')
                    else:
                        time.sleep(2 ** attempt)
        if i % 100 == 0:
            print(f'   ...{i}/{len(by_gid)} game_ids  ({ok} rows)')
        time.sleep(0.05)
    print(f'\n  wrote {ok}, failed {fail}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
