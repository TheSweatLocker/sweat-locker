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
    # 2026-09-20 PROVENANCE. Everything this script produces is rebuilt
    # after the fact from a mutable source table, so it is stamped
    # 'reconstructed' — never 'live'. A publisher writing a receipt at
    # publish time is the only thing entitled to claim 'live', and that
    # has to be an affirmative claim, not a default.
    #
    # Stamped here rather than in each of the four source functions so a
    # new source cannot forget it.
    # 2026-09-20: the integer-coercion + market-clamp logic moved to
    # public_receipt.sanitize so live capture and this backfill share ONE
    # implementation. Two copies of a normaliser drift, and a receipt
    # normalised differently depending on who wrote it is worse than no
    # normalisation at all.
    from public_receipt import sanitize as _sanitize
    for _r in rows:
        _r.setdefault('capture_mode', 'reconstructed')
        _sanitize(_r)
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
        # Pull tier from the source prop for a snapshot
        # (tier not in prop_jerry_reads itself)
        # We use call_verdict as a proxy — BACK/FADE/PASS.
        verdict = (row.get('call_verdict') or '').upper() or None
        # 2026-09-25: record the side we BACKED, not the prop's own side.
        # On a FADE we bet the opposite of the prop, but this wrote the prop's
        # direction regardless — so all 72 graded FADE prop receipts displayed
        # e.g. "Jose Quintana UNDER 10.5" while carrying the OVER's win/loss.
        # The receipt contradicted itself, and a user reading it saw the wrong
        # pick. grade_prop_jerry_reads already flips for FADE when grading
        # (flip_for_fade), so the RESULT was right and only the label was
        # wrong — which is the hardest version to notice.
        _backed = direction
        if verdict == 'FADE' and direction in ('over', 'under'):
            _backed = 'under' if direction == 'over' else 'over'
        pick_side = _backed.upper() if _backed in ('over', 'under') else None
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
            'game_id': row.get('game_id'),   # 2026-09-20: joinability
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
                f"{(_backed or direction).upper()} {prop_line} {row.get('prop_type','')}"
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
            'game_id': row.get('game_id'),   # 2026-09-20: joinability
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
    # 2026-09-20: was `sport=eq.{sport}` selecting `sport,combo_type,
    # total_odds` — none of those columns exist on ledger_snapshots. The
    # real names are sport_scope / kind / combined_odds. Every call
    # returned 42703 and the function reported "scanned=0 written=0",
    # which reads exactly like "no ledger rows to backfill".
    #
    # Consequence: the Ledger has NEVER had a single receipt written, for
    # any sport, since public_receipts shipped. The one surface Andy
    # flagged as underperforming is the one with no evidence trail at all.
    #
    # sport_scope holds values like 'MLB' / 'MULTI', so an exact match on
    # the sport is right for single-sport rows; MULTI rows are handled by
    # the daily_degen path.
    url = (f'{SB}/rest/v1/ledger_snapshots?sport_scope=eq.{sport}'
           f'&select=id,sport_scope,game_date,kind,legs,legs_hit,result,'
           f'snapshotted_at,graded_at,combined_odds,unit_pnl'
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
        odds = row.get('combined_odds')
        try:
            odds_int = int(odds) if odds is not None else None
        except (TypeError, ValueError):
            odds_int = None
        kind = row.get('kind') or 'parlay'
        hit = row.get('legs_hit')
        batch.append({
            'sport': sport,
            'surface': 'ledger',
            'market': kind,
            'game_date': row.get('game_date'),
            'published_at': row.get('snapshotted_at'),
            'player_name': None,
            'prop_type': None,
            'pick_side': None,
            'pick_line': None,
            'pick_odds': odds_int,
            'matchup': None,
            'pick_label': (f"{kind} ({leg_count or '?'} legs"
                           + (f", {hit} hit)" if hit is not None else ')')),
            'tier': None,
            'conviction': None,
            'result': result,
            'actual_value': None,
            'graded_at': row.get('graded_at'),
            'source_table': 'ledger_snapshots',
            'source_id': str(row.get('id')),
            # Keep the legs themselves — a parlay receipt without its legs
            # cannot be audited, and unit_pnl is the only place the actual
            # P&L of that ticket survives.
            'audit': {'legs': legs, 'legs_hit': hit,
                      'unit_pnl': row.get('unit_pnl')} if legs else None,
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


# ─── Source: jerry_cache sharp_card_* ──────────────────────────────

def backfill_sharp_card(dry_run: bool = False) -> int:
    """The Sharp (Steam Room slate) — 0 receipts before 2026-09-20.

    This is the surface whose record gets quoted publicly, and it had no
    evidence trail at all. jerry_cache keeps the published payload per
    day, so the history IS recoverable: 18 days / 410 picks back to
    2026-09-03.

    Stamped 'reconstructed' like every other backfill — it is rebuilt
    after the fact. generate_sharp_card now writes 'live' receipts at
    publish, and first-write-wins means this can never overwrite one.
    Shares the row adapter with the live path so the two produce
    identical shapes.
    """
    from public_receipt import sharp_card_rows
    rows = []
    # paged() yields individual rows, not pages.
    for c in paged(f'{SB}/rest/v1/jerry_cache?select=cache_key,data'
                   f'&cache_key=like.sharp_card_%'):
        key = str(c.get('cache_key') or '')
        game_date = key.replace('sharp_card_', '').strip()
        if len(game_date) != 10:
            continue
        items = ((c.get('data') or {}).get('items')) or []
        rows.extend(sharp_card_rows(items, game_date))
    # Multi-sport surface: rows already carry their own per-item sport.
    n = upsert_batch(rows, dry_run)
    print(f'  sharp_card: {n}/{len(rows)} receipts from jerry_cache')
    return n


# ─── Source: jerry_cache sweat_card_* ──────────────────────────────

def backfill_sweat_card(dry_run: bool = False) -> int:
    """The Sweat Card (dashboard top-8 + football) — 0 receipts before now.

    141 cached days / 876 picks reaching back to 2026-05-22, which is
    further than most live tables survive: mlb_game_context retains only
    195 rows. The published card payload is the best record of what users
    actually saw in June/July/August.

    Stamped 'reconstructed'. generate_sweat_card writes 'live' at publish
    from here on, and first-write-wins means this never overwrites one.
    """
    from public_receipt import sweat_card_rows
    rows = []
    for c in paged(f'{SB}/rest/v1/jerry_cache?select=cache_key,data'
                   f'&cache_key=like.sweat_card_%'):
        game_date = str(c.get('cache_key') or '').replace('sweat_card_', '').strip()
        if len(game_date) != 10:
            continue
        rows.extend(sweat_card_rows(c.get('data') or {}, game_date))
    n = upsert_batch(rows, dry_run)
    print(f'  sweat_card: {n}/{len(rows)} receipts from jerry_cache')
    return n


# ─── Source: daily_best_bet_history (POTD) ─────────────────────────

def backfill_potd(dry_run: bool = False) -> int:
    """Pick of the Day — 132 rows back to 2026-04-10, already graded
    (70-52-4). The deepest graded record we hold anywhere."""
    from public_receipt import potd_rows
    recs = list(paged(f'{SB}/rest/v1/daily_best_bet_history?select=*'))
    rows = potd_rows(recs)
    n = upsert_batch(rows, dry_run)
    print(f'  potd: {n}/{len(rows)} receipts from daily_best_bet_history')
    return n


# ─── Source: daily_dawg (Dawg of the Day) ──────────────────────────

def backfill_dawg(dry_run: bool = False) -> int:
    """Dawg of the Day — 123 rows back to 2026-04-22, already graded."""
    from public_receipt import dawg_rows
    recs = list(paged(f'{SB}/rest/v1/daily_dawg?select=*'))
    rows = dawg_rows(recs)
    n = upsert_batch(rows, dry_run)
    print(f'  dawg: {n}/{len(rows)} receipts from daily_dawg')
    return n


# ─── Main ─────────────────────────────────────────────────────────

SOURCES = {
    'prop_jerry_reads': lambda sport, dry: backfill_prop_jerry(sport, dry),
    'jerry_reads':      lambda sport, dry: backfill_jerry_reads(sport, dry),
    'ledger_snapshots': lambda sport, dry: backfill_ledger(sport, dry),
    'daily_degen':      lambda sport, dry: backfill_daily_degen(dry),
    'sharp_card':       lambda sport, dry: backfill_sharp_card(dry),
    'sweat_card':       lambda sport, dry: backfill_sweat_card(dry),
    'potd':             lambda sport, dry: backfill_potd(dry),
    'dawg':             lambda sport, dry: backfill_dawg(dry),
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
