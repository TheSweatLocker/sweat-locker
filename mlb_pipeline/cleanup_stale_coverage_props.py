"""Stale-COVERAGE prop cleanup — 2026-09-12.

Andy caught this class earlier tonight: **Tyler Mahle** showed as
"LEAN 56 ha_over 4.5" on the Prop Jerry card. But his mlb_pipeline_
props tier had been demoted to COVERAGE 0 with null signals (player
scratched from the game). The stale prop_jerry_reads row from
earlier in the day, when he was LEAN, kept surfacing on the app.

Root cause: prop_jerry_reads doesn't auto-clean when its parent
mlb_pipeline_props tier drops to COVERAGE. Once a synth pass writes
the row at LEAN/STRONG/PRIME, it lingers even after the underlying
prop demotes. Grader can only work on it if the parent still fires
signals — and COVERAGE-demoted props usually have _stat_last10=None
too, so the read shows no graph AND stale conviction. Terrible UX.

This script:
  1. Pages every COVERAGE-tier prop for today across all sports
  2. Looks up the matching prop_jerry_reads row
  3. If conviction >= 55, DELETES the prop_jerry_reads (post-lock the
     display simply shows no prop card for that player, which is
     correct — the ensemble no longer wants to publish)
  4. Reports each deletion + a running count

Runs after each cron cycle that touches mlb_pipeline_props (scoring,
refit, dedup). Deletion is safe — grader looks up by (player, type,
direction) still, and if the prop later resurrects (rare), synth
can re-render.

USAGE:
    python cleanup_stale_coverage_props.py                # today, all sports
    python cleanup_stale_coverage_props.py --sport MLB
    python cleanup_stale_coverage_props.py --dry-run
    python cleanup_stale_coverage_props.py --date 2026-09-14
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
K = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# 2026-09-12 CONVICTION FLOOR for cleanup. Anything >= this on a
# COVERAGE prop is "stale-loud" — was ranked worth publishing at
# some earlier point, now demoted. Below this floor the row was
# never card-worthy anyway; no user impact from leaving it.
STALE_CONVICTION_FLOOR = 55


PROPS_TABLES = {
    'MLB':   'mlb_pipeline_props',
    'NFL':   'nfl_pipeline_props',
    'NCAAF': 'ncaaf_pipeline_props',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _paged(table: str, date: str, tier: str = 'COVERAGE') -> list[dict]:
    """Range-header pagination for COVERAGE-tier props on a date."""
    out = []
    for page in range(6):  # 6*1000 = 6k safety cap
        lo = page * 1000; hi = lo + 999
        r = requests.get(f'{SB}/rest/v1/{table}',
            headers={**H_READ, 'Range': f'{lo}-{hi}', 'Range-Unit': 'items'},
            params={'game_date': f'eq.{date}', 'tier': f'eq.{tier}',
                    'select': 'player_name,prop_type,direction,tier'},
            timeout=30)
        if r.status_code not in (200, 206): break
        chunk = r.json() if isinstance(r.json(), list) else []
        out.extend(chunk)
        if len(chunk) < 1000: break
    return out


def scan_and_clean(sport: str, date: str, dry_run: bool = False) -> int:
    """Returns count of stale prop_jerry_reads deleted."""
    table = PROPS_TABLES.get(sport)
    if not table:
        print(f'  [{sport}] no props table registered — skip'); return 0
    cov_props = _paged(table, date)
    print(f'  [{sport}] {len(cov_props)} COVERAGE-tier props scanned')
    stale_ids = []
    stale_details = []
    for row in cov_props:
        j = requests.get(f'{SB}/rest/v1/prop_jerry_reads', headers=H_READ,
            params={'sport': f'eq.{sport}', 'game_date': f'eq.{date}',
                    'player_name': f'eq.{row["player_name"]}',
                    'prop_type': f'eq.{row["prop_type"]}',
                    'direction': f'eq.{row["direction"]}',
                    'select': 'id,conviction,call_verdict,generated_at'},
            timeout=8)
        jrows = j.json() if isinstance(j.json(), list) else []
        for jr in jrows:
            conv = int(jr.get('conviction') or 0)
            if conv >= STALE_CONVICTION_FLOOR:
                stale_ids.append(jr['id'])
                stale_details.append(
                    f'{row["player_name"][:24]:24s} {row["prop_type"]:14s} '
                    f'{row["direction"]:5s}  pjr_conv={conv}  '
                    f'verdict={jr.get("call_verdict","?")}'
                )
    if not stale_ids:
        print(f'  [{sport}] ✓ no stale-COVERAGE prop_jerry_reads found')
        return 0
    print(f'  [{sport}] {len(stale_ids)} STALE prop_jerry_reads (conv>={STALE_CONVICTION_FLOOR}):')
    for d in stale_details[:15]: print(f'    · {d}')
    if len(stale_details) > 15:
        print(f'    ...and {len(stale_details) - 15} more')
    if dry_run:
        print(f'  [DRY-RUN] would delete {len(stale_ids)} rows')
        return 0
    # Delete in chunks of 100
    deleted = 0
    for i in range(0, len(stale_ids), 100):
        ids_csv = ','.join(str(x) for x in stale_ids[i:i+100])
        r = requests.delete(f'{SB}/rest/v1/prop_jerry_reads?id=in.({ids_csv})',
            headers=H_WRITE, timeout=30)
        if r.status_code in (200, 204):
            deleted += min(100, len(stale_ids) - i)
        else:
            print(f'  [{sport}] delete chunk failed: {r.status_code} {r.text[:150]}')
    print(f'  [{sport}] deleted {deleted} stale rows')
    return deleted


def scan_stale_jerry_reads(sport: str, date: str, dry_run: bool = False) -> int:
    """2026-09-13 v2: jerry-read-first orphan sweep.

    Sister to scan_and_clean() (which walks the props table for
    COVERAGE tier). This variant walks prop_jerry_reads directly and
    kills any row where the parent mlb_pipeline_props table has NO
    publishable tier for that (player, prop_type, direction) tuple —
    catches the SKIP-tier orphan class the COVERAGE-only scan misses.

    Motivation: Andy audit 9/12 — Ian Seymour ha_over LEAN jerry_read
    exists but every mlb_pipeline_props row for that tuple is SKIP.
    Same story for Gage Jump / Andrew Alvarez / Brady Singer LEAN
    pitcher props (8 total on today's slate). App renders these as
    "empty L5/L10" cards because the parent prop has no recent-form
    signals attached — the jerry_read is from an earlier tier state
    and needs to go.
    """
    table = PROPS_TABLES.get(sport)
    if not table:
        print(f'  [{sport}] no props table registered — skip'); return 0
    # Page all publishable-conviction jerry_reads for the date+sport
    jerry_reads = []
    for page in range(15):
        lo = page * 1000
        r = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
            headers={**H_READ, 'Range': f'{lo}-{lo+999}', 'Range-Unit': 'items'},
            params={'sport': f'eq.{sport}', 'game_date': f'eq.{date}',
                    'conviction': f'gte.{STALE_CONVICTION_FLOOR}',
                    'select': 'id,player_name,prop_type,direction,conviction,call_verdict'},
            timeout=20)
        if r.status_code not in (200, 206): break
        chunk = r.json() if isinstance(r.json(), list) else []
        jerry_reads.extend(chunk)
        if len(chunk) < 1000: break
    print(f'  [{sport}] {len(jerry_reads)} publishable jerry_reads to verify')
    # Build a set of (player, prop_type, direction) with a live publishable
    # parent prop. One paged scan covers the whole slate.
    #
    # 2026-09-13 v2 REFINE: include COVERAGE tier as an alive parent — the
    # composer's publishable-tier set is ('PRIME','STRONG','LEAN','COVERAGE'),
    # so a jerry_read with a COVERAGE parent is legitimately publishable and
    # must not be swept. The original scan_and_clean() only kills a jerry_
    # read when the COVERAGE parent's own conviction dropped below the
    # STALE_CONVICTION_FLOOR — this jerry-orphan variant kills only when
    # NO parent at ANY publishable tier exists (or parent is SKIP-only).
    alive = set()
    for tier in ('PRIME', 'STRONG', 'LEAN', 'COVERAGE'):
        for page in range(15):
            lo = page * 1000
            r = requests.get(f'{SB}/rest/v1/{table}',
                headers={**H_READ, 'Range': f'{lo}-{lo+999}', 'Range-Unit': 'items'},
                params={'game_date': f'eq.{date}', 'tier': f'eq.{tier}',
                        'select': 'player_name,prop_type,direction'},
                timeout=20)
            if r.status_code not in (200, 206): break
            chunk = r.json() if isinstance(r.json(), list) else []
            for row in chunk:
                alive.add((row['player_name'], row['prop_type'], row['direction']))
            if len(chunk) < 1000: break
    print(f'  [{sport}] {len(alive)} live publishable (player,type,dir) tuples')
    # Any jerry_read whose parent tuple isn't in `alive` is stale
    stale = [jr for jr in jerry_reads
             if (jr['player_name'], jr['prop_type'], jr['direction']) not in alive]
    if not stale:
        print(f'  [{sport}] ✓ no orphaned jerry_reads')
        return 0
    print(f'  [{sport}] {len(stale)} ORPHANED jerry_reads (parent prop demoted below LEAN):')
    for jr in stale[:15]:
        print(f'    · {jr["player_name"][:24]:24s} {jr["prop_type"]:14s} '
              f'{jr["direction"]:5s}  pjr_conv={jr["conviction"]}  '
              f'verdict={jr.get("call_verdict","?")}')
    if len(stale) > 15:
        print(f'    ...and {len(stale) - 15} more')
    if dry_run:
        print(f'  [DRY-RUN] would delete {len(stale)} rows')
        return 0
    deleted = 0
    ids = [jr['id'] for jr in stale]
    for i in range(0, len(ids), 100):
        chunk_ids = ids[i:i+100]
        csv = ','.join(str(x) for x in chunk_ids)
        r = requests.delete(f'{SB}/rest/v1/prop_jerry_reads?id=in.({csv})',
            headers=H_WRITE, timeout=30)
        if r.status_code in (200, 204):
            deleted += len(chunk_ids)
        else:
            print(f'  [{sport}] delete chunk failed: {r.status_code} {r.text[:150]}')
    print(f'  [{sport}] deleted {deleted} orphaned rows')
    return deleted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', help='One sport (MLB/NFL/NCAAF); default all')
    ap.add_argument('--date', help='ET date; default today')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--jerry-orphan-scan', action='store_true',
                    help='Use jerry-read-first orphan detection (catches SKIP + '
                         'COVERAGE + missing-parent classes at once). Recommended.')
    args = ap.parse_args()
    d = args.date or _et_today()
    sports = [args.sport] if args.sport else list(PROPS_TABLES.keys())
    print(f'=== cleanup_stale_coverage_props · {d} '
          f'{"[DRY-RUN]" if args.dry_run else ""} '
          f'{"[JERRY-ORPHAN]" if args.jerry_orphan_scan else "[COVERAGE-SCAN]"} ===')
    total = 0
    for s in sports:
        if args.jerry_orphan_scan:
            total += scan_stale_jerry_reads(s, d, dry_run=args.dry_run)
        else:
            total += scan_and_clean(s, d, dry_run=args.dry_run)
    print(f'\n=== total stale rows {"would-delete" if args.dry_run else "deleted"}: {total} ===')


if __name__ == '__main__':
    main()
