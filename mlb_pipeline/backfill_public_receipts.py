"""Backfill public_receipts from historical composition tables.

Andy directive 2026-09-18: "each pick needs to be logged and stored
somewhere ... records on records on records." This script reconstructs
the historical user-visible pick log from the tables that actually
recorded what was surfaced to users:

    prop_jerry_reads   → 9,294 MLB rows (surface='prop_jerry')
    jerry_reads        → 648 MLB rows   (surface='game_read' or 'potd')
    ledger_snapshots   → per-day ledger parlays (surface='ledger')
    daily_degen        → per-day degen picks (surface='daily_degen')

Writes to public_receipts with ON CONFLICT DO NOTHING (idempotent —
safe to re-run). Result field copied from source when populated.

Does NOT touch surface_records / daily_surface_records. Delta between
"what surface_records claims" vs "what public_receipts proves" is
computed by receipts_report.py — Andy reviews before we flip the
aggregator source.

Usage:
    # Backfill everything
    python backfill_public_receipts.py --sport MLB

    # Backfill one source only
    python backfill_public_receipts.py --sport MLB --source prop_jerry_reads

    # Dry-run — count what would land
    python backfill_public_receipts.py --sport MLB --dry-run

2026-09-18: Phase 1 of public_receipts integrity project.
See memory/project_public_receipts_integrity_918.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

_ENV = Path(__file__).parent / '.env'
if _ENV.exists():
    for line in _ENV.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if not SB or not KEY:
    print('SUPABASE_URL / SUPABASE_KEY not set — refusing to run.')
    sys.exit(1)

H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           # ON CONFLICT DO NOTHING via ignore-duplicates; first write wins.
           'Prefer': 'resolution=ignore-duplicates,return=minimal'}


# ─── Paged read helper ─────────────────────────────────────────────

def paged(url: str, page_size: int = 1000):
    offset = 0
    while True:
        r = requests.get(url + f'&offset={offset}&limit={page_size}',
                         headers=H_READ, timeout=60)
        if r.status_code != 200:
            print(f'  fetch failed {r.status_code}: {r.text[:200]}')
            return
        rows = r.json()
        if not rows:
            return
        for row in rows:
            if isinstance(row, dict):
                yield row
        if len(rows) < page_size:
            return
        offset += page_size


# ─── Batch upsert helper ───────────────────────────────────────────

def upsert_batch(rows: list[dict], dry_run: bool = False) -> int:
    if not rows:
        return 0
    if dry_run:
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/public_receipts?on_conflict=sport,surface,game_date,source_id',
        headers=H_WRITE, json=rows, timeout=60,
    )
    if r.status_code in (200, 201, 204):
        return len(rows)
    print(f'  ⚠ batch write {r.status_code}: {r.text[:300]}')
    return 0


# ─── Source: prop_jerry_reads ──────────────────────────────────────

def backfill_prop_jerry(sport: str, dry_run: bool = False) -> int:
    """Every Prop Jerry synthesized read = a user-visible prop pick."""
    print(f'\n=== backfill_prop_jerry · {sport} ===')
    url = (f'{SB}/rest/v1/prop_jerry_reads?sport=eq.{sport}'
           f'&select=id,sport,game_id,game_date,player_name,prop_type,'
           f'direction,prop_line,conviction,book_odds,call_verdict,result,'
           f'resolved_at,generated_at&order=game_date.desc')
    batch = []
    written = 0
    scanned = 0
    for row in paged(url):
        scanned += 1
        prop_line = row.get('prop_line')
        odds = row.get('book_odds')
        try:
            odds_int = int(odds) if odds is not None else None
        except (TypeError, ValueError):
            odds_int = None
        direction = (row.get('direction') or '').lower()
        pick_side = direction.upper() if direction in ('over', 'under') else None
        # Pull tier from the source prop for a snapshot
        # (tier not in prop_jerry_reads itself)
        # We use call_verdict as a proxy — BACK/FADE/PASS.
        verdict = (row.get('call_verdict') or '').upper() or None
        result = (row.get('result') or None)
        # Normalize result
        if result:
            r_low = result.lower()
            if r_low in ('w', 'win'): result = 'WIN'
            elif r_low in ('l', 'loss'): result = 'LOSS'
            elif r_low in ('p', 'push'): result = 'PUSH'
            elif r_low in ('v', 'void'): result = 'VOID'
            else: result = result.upper()
        batch.append({
            'sport': sport,
            'surface': 'prop_jerry',
            'market': 'prop',
            'game_date': row.get('game_date'),
            'published_at': row.get('generated_at'),
            'player_name': row.get('player_name'),
            'prop_type': row.get('prop_type'),
            'pick_side': pick_side,
            'pick_line': prop_line,
            'pick_odds': odds_int,
            'matchup': None,
            'pick_label': (
                f"{row.get('player_name','')} "
                f"{direction.upper()} {prop_line} {row.get('prop_type','')}"
                f" @ {odds_int if odds_int else '?'}"
            ),
            'tier': None,           # prop_jerry_reads doesn't carry tier — reconstructable via mlb_pipeline_props if needed
            'conviction': row.get('conviction'),
            'result': result,
            'actual_value': None,
            'graded_at': row.get('resolved_at'),
            'source_table': 'prop_jerry_reads',
            'source_id': str(row.get('id')),
            'audit': {'call_verdict': verdict} if verdict else None,
        })
        if len(batch) >= 500:
            written += upsert_batch(batch, dry_run)
            batch = []
            print(f'  scanned={scanned}  written={written} ...')
    written += upsert_batch(batch, dry_run)
    print(f'  scanned={scanned}  written={written}')
    return written


# ─── Source: jerry_reads (game side/total + POTD) ───────────────────

def backfill_jerry_reads(sport: str, dry_run: bool = False) -> int:
    print(f'\n=== backfill_jerry_reads · {sport} ===')
    # Get is_potd if column exists; fallback to null
    url = (f'{SB}/rest/v1/jerry_reads?sport=eq.{sport}'
           f'&select=id,sport,game_id,game_date,call_market,call_side,'
           f'call_line,call_text,conviction,result,generated_at,resolved_at'
           f'&order=game_date.desc')
    batch = []
    written = 0
    scanned = 0
    for row in paged(url):
        scanned += 1
        result = row.get('result')
        if result:
            r_low = str(result).lower()
            if r_low in ('w', 'win'): result = 'WIN'
            elif r_low in ('l', 'loss'): result = 'LOSS'
            elif r_low in ('p', 'push'): result = 'PUSH'
            elif r_low in ('v', 'void'): result = 'VOID'
            else: result = str(result).upper()
        try:
            odds_int = None  # jerry_reads doesn't carry odds explicitly
            line_val = float(row.get('call_line')) if row.get('call_line') is not None else None
        except (TypeError, ValueError):
            line_val = None
        market = row.get('call_market') or ''
        # 'pass' verdict = Jerry decided to pass — skip as receipt (was not a pick)
        if market == 'pass':
            continue
        batch.append({
            'sport': sport,
            'surface': 'game_read',       # POTD promotion handled separately
            'market': market or 'game',
            'game_date': row.get('game_date'),
            'published_at': row.get('generated_at'),
            'player_name': None,
            'prop_type': None,
            'pick_side': (row.get('call_side') or '').upper() or None,
            'pick_line': line_val,
            'pick_odds': odds_int,
            'matchup': None,              # would need ctx join; leave for downstream enrichment
            'pick_label': row.get('call_text'),
            'tier': None,
            'conviction': row.get('conviction'),
            'result': result,
            'actual_value': None,
            'graded_at': row.get('resolved_at'),
            'source_table': 'jerry_reads',
            'source_id': str(row.get('id')),
            'audit': None,
        })
        if len(batch) >= 500:
            written += upsert_batch(batch, dry_run)
            batch = []
            print(f'  scanned={scanned}  written={written} ...')
    written += upsert_batch(batch, dry_run)
    print(f'  scanned={scanned}  written={written}')
    return written


# ─── Source: ledger_snapshots (parlays) ────────────────────────────

def backfill_ledger(sport: str, dry_run: bool = False) -> int:
    print(f'\n=== backfill_ledger_snapshots · {sport} ===')
    url = (f'{SB}/rest/v1/ledger_snapshots?sport=eq.{sport}'
           f'&select=id,sport,game_date,combo_type,legs,result,created_at,resolved_at,total_odds'
           f'&order=game_date.desc')
    batch = []
    written = 0
    scanned = 0
    for row in paged(url):
        scanned += 1
        result = row.get('result')
        if result:
            r_low = str(result).lower()
            if r_low in ('w', 'win'): result = 'WIN'
            elif r_low in ('l', 'loss'): result = 'LOSS'
            elif r_low in ('p', 'push'): result = 'PUSH'
            else: result = str(result).upper()
        legs = row.get('legs') or []
        leg_count = len(legs) if isinstance(legs, list) else None
        odds = row.get('total_odds')
        try:
            odds_int = int(odds) if odds is not None else None
        except (TypeError, ValueError):
            odds_int = None
        batch.append({
            'sport': sport,
            'surface': 'ledger',
            'market': row.get('combo_type') or 'parlay',
            'game_date': row.get('game_date'),
            'published_at': row.get('created_at'),
            'player_name': None,
            'prop_type': None,
            'pick_side': None,
            'pick_line': None,
            'pick_odds': odds_int,
            'matchup': None,
            'pick_label': f"{row.get('combo_type','parlay')} ({leg_count or '?'} legs)",
            'tier': None,
            'conviction': None,
            'result': result,
            'actual_value': None,
            'graded_at': row.get('resolved_at'),
            'source_table': 'ledger_snapshots',
            'source_id': str(row.get('id')),
            'audit': {'legs': legs} if legs else None,
        })
        if len(batch) >= 500:
            written += upsert_batch(batch, dry_run)
            batch = []
            print(f'  scanned={scanned}  written={written} ...')
    written += upsert_batch(batch, dry_run)
    print(f'  scanned={scanned}  written={written}')
    return written


# ─── Source: daily_degen ───────────────────────────────────────────

def backfill_daily_degen(dry_run: bool = False) -> int:
    print(f'\n=== backfill_daily_degen (multi-sport blended) ===')
    url = (f'{SB}/rest/v1/daily_degen?select=game_date,legs,result,'
           f'avg_conviction,created_at&order=game_date.desc')
    batch = []
    written = 0
    scanned = 0
    for row in paged(url):
        scanned += 1
        result = row.get('result')
        if result:
            r_low = str(result).lower()
            if r_low in ('w', 'win'): result = 'WIN'
            elif r_low in ('l', 'loss'): result = 'LOSS'
            elif r_low in ('p', 'push'): result = 'PUSH'
            else: result = str(result).upper()
        legs = row.get('legs') or []
        # Daily degen is cross-sport — use MULTI sport code
        batch.append({
            'sport': 'MULTI',
            'surface': 'daily_degen',
            'market': 'parlay',
            'game_date': row.get('game_date'),
            'published_at': row.get('created_at'),
            'player_name': None,
            'prop_type': None,
            'pick_side': None,
            'pick_line': None,
            'pick_odds': None,
            'matchup': None,
            'pick_label': f'daily_degen ({len(legs) if isinstance(legs,list) else "?"} legs)',
            'tier': None,
            'conviction': row.get('avg_conviction'),
            'result': result,
            'actual_value': None,
            'graded_at': None,
            'source_table': 'daily_degen',
            'source_id': row.get('game_date'),   # one row per date
            'audit': {'legs': legs} if legs else None,
        })
        if len(batch) >= 500:
            written += upsert_batch(batch, dry_run)
            batch = []
    written += upsert_batch(batch, dry_run)
    print(f'  scanned={scanned}  written={written}')
    return written


# ─── Main ─────────────────────────────────────────────────────────

SOURCES = {
    'prop_jerry_reads': lambda sport, dry: backfill_prop_jerry(sport, dry),
    'jerry_reads':      lambda sport, dry: backfill_jerry_reads(sport, dry),
    'ledger_snapshots': lambda sport, dry: backfill_ledger(sport, dry),
    'daily_degen':      lambda sport, dry: backfill_daily_degen(dry),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB', choices=['MLB', 'NFL', 'NCAAF', 'NBA', 'NHL', 'NCAAB', 'UFC'])
    p.add_argument('--source', choices=list(SOURCES.keys()) + ['all'], default='all')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    print(f'=== backfill_public_receipts · sport={args.sport} · source={args.source} '
          f'{"(DRY)" if args.dry_run else "(APPLY)"} · {datetime.now().isoformat()} ===')

    if args.source == 'all':
        for name, fn in SOURCES.items():
            try:
                fn(args.sport, args.dry_run)
            except Exception as e:
                print(f'  ✗ {name} failed: {e}')
    else:
        SOURCES[args.source](args.sport, args.dry_run)

    print(f'\n=== done · {datetime.now().isoformat()} ===')


if __name__ == '__main__':
    main()
