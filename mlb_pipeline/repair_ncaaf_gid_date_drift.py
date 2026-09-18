"""Repair NCAAF ctx rows whose game_id embeds the wrong date.

ROOT CAUSE (fixed at write-path 2026-09-16, ncaaf_odds_pull.py:163-174):
Prior puller built game_id from the UTC date while game_date was set from
the ET date. Thu/Fri night kickoffs (8pm+ ET) cross UTC midnight, so the
id landed one day ahead of game_date. A later pull with a slightly
different commence_time produced a SECOND row under the correct id —
same game, two rows, conflicting spread/tier. The composer sorts by
conviction, so a stale-but-higher-conviction row can beat today's fresh
read (2026-09-18: Sharp Card published Oregon -56.5 STRONG/77 from a
9/15 row while the live row said -58.5 LEAN/62).

The write path is fixed. This script cleans up what the old path left
and is a no-op once the data is clean.

TWO CASES
  A. orphan HAS a correct-id twin  -> repoint jerry_reads to the twin,
     then delete the orphan ctx row.
     Collision: if the twin already owns a jerry_read for the same
     (game_id, game_date), keep the newer by generated_at and delete
     the orphan's read rather than creating a duplicate.
  B. orphan has NO twin -> the row is the only record of a real game.
     Rewrite its game_id to the ET-anchored form and repoint its
     jerry_reads to match.

Downstream scope verified 2026-09-18: jerry_reads is the only table
carrying these ids (prop_jerry_reads/external_picks scanned, 0 hits;
NCAAF has no props per feedback_college_sports_no_props).

CLI:
    python repair_ncaaf_gid_date_drift.py --dry-run
    python repair_ncaaf_gid_date_drift.py --apply
"""
from __future__ import annotations
import argparse, os, re, sys
from collections import defaultdict
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
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

_GID_DATE = re.compile(r'(20\d{6})')


def gid_date(game_id: str) -> str | None:
    """Extract the YYYY-MM-DD the game_id claims, or None if unparseable."""
    m = _GID_DATE.search(str(game_id or ''))
    if not m: return None
    d = m.group(1)
    return f'{d[:4]}-{d[4:6]}-{d[6:]}'


def correct_gid(row: dict) -> str:
    """Rebuild the id with the row's own game_date (ET-anchored)."""
    return _GID_DATE.sub(row['game_date'].replace('-', ''), row['game_id'], count=1)


def page(table: str, select: str, extra: str = '') -> list:
    out = []; off = 0
    while off < 50000:
        r = requests.get(f'{SB}/rest/v1/{table}?select={select}{extra}'
                         f'&limit=1000&offset={off}', headers=H_READ, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f'{table} fetch {r.status_code}: {r.text[:200]}')
        batch = r.json()
        if not batch: break
        out.extend(batch)
        if len(batch) < 1000: break
        off += 1000
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    dry = args.dry_run

    ctx = page('ncaaf_game_context', 'game_id,game_date,home_team,away_team,close_spread,updated_at')
    reads = page('jerry_reads', 'id,game_id,game_date,generated_at', '&sport=eq.NCAAF')
    print(f'=== NCAAF gid/date drift repair {"(DRY RUN)" if dry else "(APPLY)"} ===')
    print(f'ctx rows={len(ctx)}  NCAAF jerry_reads={len(reads)}')

    reads_by_gid = defaultdict(list)
    for r in reads:
        reads_by_gid[r['game_id']].append(r)

    by_match = defaultdict(list)
    for row in ctx:
        by_match[(row['game_date'],
                  (row.get('home_team') or '').strip(),
                  (row.get('away_team') or '').strip())].append(row)

    orphans = [r for r in ctx
               if gid_date(r['game_id']) and gid_date(r['game_id']) != r['game_date']]
    print(f'orphans (gid date != game_date): {len(orphans)}\n')

    case_a, case_b = [], []
    for o in orphans:
        key = (o['game_date'], (o.get('home_team') or '').strip(),
               (o.get('away_team') or '').strip())
        twins = [s for s in by_match[key]
                 if s['game_id'] != o['game_id']
                 and gid_date(s['game_id']) == s['game_date']]
        (case_a if twins else case_b).append((o, twins[0] if twins else None))

    print(f'CASE A · orphan has correct twin -> repoint reads + delete orphan: {len(case_a)}')
    print(f'CASE B · orphan is sole record   -> rewrite game_id + repoint reads: {len(case_b)}\n')

    repointed = deleted_reads = deleted_ctx = rewritten = merged = 0

    # ---- CASE A ----
    for orphan, twin in case_a:
        o_gid, t_gid = orphan['game_id'], twin['game_id']
        o_reads = reads_by_gid.get(o_gid, [])
        t_reads = reads_by_gid.get(t_gid, [])
        print(f'  [A] {orphan["away_team"]} @ {orphan["home_team"]} ({orphan["game_date"]})')
        print(f'      orphan {o_gid}  spread={orphan.get("close_spread")}  upd={str(orphan.get("updated_at"))[:19]}')
        print(f'      twin   {t_gid}  spread={twin.get("close_spread")}  upd={str(twin.get("updated_at"))[:19]}')

        # One read survives per game: the newest across orphan + twin.
        # Everything else goes, so repointing can't create a duplicate.
        pair = o_reads + t_reads
        if pair:
            keeper = max(pair, key=lambda x: str(x.get('generated_at') or ''))
            for cand in pair:
                if cand['id'] == keeper['id']: continue
                print(f'      read {cand["id"]} -> DELETE (older dup of {keeper["id"]})')
                if not dry:
                    requests.delete(f'{SB}/rest/v1/jerry_reads?id=eq.{cand["id"]}',
                                    headers=H_WRITE, timeout=15)
                deleted_reads += 1
            if keeper['game_id'] != t_gid:
                print(f'      read {keeper["id"]} -> REPOINT {o_gid} => {t_gid}')
                if not dry:
                    requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{keeper["id"]}',
                                   headers=H_WRITE, json={'game_id': t_gid}, timeout=15)
                repointed += 1

        # The twin owns the correct id but not necessarily the fresher data.
        # (2026-09-18: Houston @ Texas Tech twin was 6 days staler than the
        # orphan.) Copy the orphan's payload onto the twin before deleting,
        # so we keep the right id AND the newest numbers.
        if str(orphan.get('updated_at') or '') > str(twin.get('updated_at') or ''):
            print(f'      orphan is NEWER -> copy payload onto twin before delete')
            if not dry:
                full = requests.get(f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.'
                                    f'{requests.utils.quote(o_gid, safe="")}&select=*',
                                    headers=H_READ, timeout=20)
                if full.status_code == 200 and full.json():
                    payload = {k: v for k, v in full.json()[0].items() if k != 'game_id'}
                    pr = requests.patch(f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.'
                                        f'{requests.utils.quote(t_gid, safe="")}',
                                        headers=H_WRITE, json=payload, timeout=20)
                    if pr.status_code not in (200, 204):
                        print(f'      ! payload copy failed {pr.status_code}: {pr.text[:120]}'
                              f' — SKIPPING delete to avoid data loss')
                        continue
                else:
                    print(f'      ! could not read orphan payload — SKIPPING delete')
                    continue
            merged += 1

        print(f'      ctx {o_gid} -> DELETE')
        if not dry:
            rr = requests.delete(f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.'
                                 f'{requests.utils.quote(o_gid, safe="")}',
                                 headers=H_WRITE, timeout=15)
            if rr.status_code not in (200, 204):
                print(f'      ! ctx delete failed {rr.status_code}: {rr.text[:120]}')
                continue
        deleted_ctx += 1

    # ---- CASE B ----
    for orphan, _ in case_b:
        o_gid = orphan['game_id']
        new_gid = correct_gid(orphan)
        if new_gid == o_gid:
            continue
        # Guard: never collide with an existing row.
        if any(c['game_id'] == new_gid for c in ctx):
            print(f'  [B] SKIP {o_gid} -> {new_gid} (target id already exists)')
            continue
        print(f'  [B] {orphan["away_team"]} @ {orphan["home_team"]} ({orphan["game_date"]})')
        print(f'      ctx {o_gid} -> REWRITE => {new_gid}')
        if not dry:
            rr = requests.patch(f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.'
                                f'{requests.utils.quote(o_gid, safe="")}',
                                headers=H_WRITE, json={'game_id': new_gid}, timeout=15)
            if rr.status_code not in (200, 204):
                print(f'      ! ctx rewrite failed {rr.status_code}: {rr.text[:120]}')
                continue
        rewritten += 1
        for orow in reads_by_gid.get(o_gid, []):
            print(f'      read {orow["id"]} -> REPOINT {o_gid} => {new_gid}')
            if not dry:
                requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{orow["id"]}',
                               headers=H_WRITE, json={'game_id': new_gid}, timeout=15)
            repointed += 1

    print(f'\n=== {"WOULD APPLY" if dry else "APPLIED"} ===')
    print(f'  ctx rows deleted (case A):    {deleted_ctx}')
    print(f'  twin payloads refreshed:      {merged}')
    print(f'  ctx game_ids rewritten (B):   {rewritten}')
    print(f'  jerry_reads repointed:        {repointed}')
    print(f'  jerry_reads deleted (dups):   {deleted_reads}')
    if dry:
        print('\nRe-run with --apply to execute.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
