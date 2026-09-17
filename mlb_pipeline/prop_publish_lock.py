"""Universal publish-lock — snapshots tier at first user-visible publish.

Andy 9/17 directive: "process to overwrite in middle of day to stop"
+ "and this needs to be sport universal."

Any composer that puts a pick on a user surface (Sweat Card, Sharp
Card, POTD, Prop Jerry, Ladder, Ledger, Daily Degen) MUST call
`lock_publish(...)` so the tier the user saw is preserved for grading.
Later mutations to the LIVE tier field (generate_props --force wipes,
LR override yo-yo, recompute passes) can never change what the record
counts. "What you saw is what we grade."

Sport-universal: writes to the shared `publish_lock` table with
(sport, market, source_id) uniqueness. First publisher wins —
idempotent on re-call (ON CONFLICT DO NOTHING).

market values:
  * 'prop'  → source_id = mlb_pipeline_props.id (str) or nfl_pipeline_props.id
  * 'ml'    → source_id = <sport>_game_context.game_id
  * 'rl'    → source_id = <sport>_game_context.game_id
  * 'total' → source_id = <sport>_game_context.game_id
  * 'ladder' / 'ledger' → source_id = ledger_snapshots.id

See supabase/migrations/20260917e_prop_tier_publish_lock.sql for schema.
Grader reads via _fetch_publish_locks in compute_surface_records.py.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional

import requests

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_WRITE = {
    'apikey': KEY,
    'Authorization': f'Bearer {KEY}',
    'Content-Type': 'application/json',
    # ON CONFLICT DO NOTHING semantics via ignore-duplicates.
    # First publisher wins; later calls no-op silently.
    'Prefer': 'resolution=ignore-duplicates,return=minimal',
}


def lock_publish(sport: str, market: str, source_id, tier: str,
                 conviction: Optional[int], published_by: str,
                 dry_run: bool = False) -> bool:
    """Snapshot tier + conviction at first user-visible publish.

    Args:
        sport: 'MLB' | 'NFL' | 'NCAAF' | 'NBA' | 'NHL' | 'NCAAB' | 'UFC'
        market: 'prop' | 'ml' | 'rl' | 'total' | 'ladder' | 'ledger'
        source_id: identifier for the pick (int or str)
        tier: the tier the composer decided at pick-time
              (PRIME / STRONG / LEAN / COVERAGE / SKIP / PASS)
        conviction: 0-100 conviction score at pick-time
        published_by: caller name — 'sweat_card' / 'sharp_card' / 'potd'
                      / 'prop_jerry' / 'ladder' / 'ledger' / 'daily_degen'

    Returns True on success (either first-lock write or idempotent
    no-op on already-locked). False on API failure.

    IMPORTANT: composers should call this AT THE MOMENT the pick
    lands on a user surface — after all LR / calibration / dedup
    passes have decided the tier. Calling too early locks in an
    intermediate tier. Correct order: compose → filter → cap →
    lock → write surface cache.
    """
    if not sport or not market or source_id is None:
        return False
    if not SB or not KEY:
        return False
    if dry_run:
        print(f'  [DRY] publish_lock sport={sport} market={market} '
              f'source_id={source_id} tier={tier} conv={conviction} by={published_by}')
        return True
    payload = {
        'sport': str(sport).upper(),
        'market': str(market).lower(),
        'source_id': str(source_id),
        'tier_at_publish': tier,
        'conviction_at_publish': int(conviction) if conviction is not None else None,
        'published_at': datetime.now(timezone.utc).isoformat(),
        'published_by': published_by,
    }
    try:
        r = requests.post(
            f'{SB}/rest/v1/publish_lock?on_conflict=sport,market,source_id',
            headers=H_WRITE, json=[payload], timeout=10,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def lock_batch(picks: list[dict], sport: str, market: str,
               published_by: str, dry_run: bool = False,
               source_id_field: str = 'id') -> tuple[int, int]:
    """Bulk lock: iterate picks list, call lock_publish for each.

    Returns (locked_count, skipped_count).

    Args:
        picks: list of dicts with tier / conviction / source_id
        sport: 'MLB' etc.
        market: 'prop' / 'ml' / 'rl' / 'total'
        published_by: caller name
        source_id_field: which dict key holds the source_id (default 'id';
                         set to 'game_id' for sides)
    """
    locked = 0
    skipped = 0
    for p in picks:
        sid = p.get(source_id_field)
        if sid is None:
            skipped += 1
            continue
        tier = (p.get('tier') or '').upper()
        conv = p.get('conviction')
        if lock_publish(sport, market, sid, tier, conv, published_by, dry_run=dry_run):
            locked += 1
        else:
            skipped += 1
    return locked, skipped


if __name__ == '__main__':
    print('prop_publish_lock module — universal publish-lock')
    print('  lock_publish(sport, market, source_id, tier, conviction, published_by)')
    print('  lock_batch(picks, sport, market, published_by, source_id_field=...)')
    print()
    print('First-publisher-wins semantics via ON CONFLICT DO NOTHING on')
    print('(sport, market, source_id). Grader reads via _fetch_publish_locks')
    print('in compute_surface_records.py — prefers locked tier over live.')
