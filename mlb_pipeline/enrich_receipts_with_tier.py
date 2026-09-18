"""Enrich public_receipts with tier/conviction/pick_odds from source props.

Andy directive 2026-09-18: "ship enrichment ... real records verified only"

prop_jerry_reads doesn't carry tier/conviction/book_odds directly.
Those live on mlb_pipeline_props. This script joins each receipt
back to its source prop via (game_date, player_name, prop_type,
prop_line, direction) and fills the missing identity fields.

The receipt table trigger (migration 20260918b) allows one-time
NULL → value transitions on tier/conviction/pick_odds/matchup —
so this enrichment is safe and can only fill, never overwrite.

Filter caveat: uses CURRENT tier from mlb_pipeline_props, not
tier-at-publish (which we didn't capture historically). If a prop
ended up tier=SKIP after being surfaced then demoted, the receipt
still gets tagged SKIP — undercounting real user visibility slightly.
Going forward, composers will write receipts with tier-at-publish
directly (Phase 4).

Usage:
    python enrich_receipts_with_tier.py --sport MLB
    python enrich_receipts_with_tier.py --sport MLB --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

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
    sys.exit('SUPABASE_URL / SUPABASE_KEY missing')

H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

PROP_TABLE = {
    'MLB':   'mlb_pipeline_props',
    'NFL':   'nfl_pipeline_props',
    'NCAAF': None,  # NCAAF no props per feedback_college_sports_no_props
}


def paged(url: str, page_size: int = 1000):
    offset = 0
    while True:
        r = requests.get(url + f'&offset={offset}&limit={page_size}', headers=H_R, timeout=60)
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


def run(sport: str, dry_run: bool = False) -> None:
    prop_tbl = PROP_TABLE.get(sport)
    if not prop_tbl:
        print(f'{sport}: no prop table registered — skip')
        return

    print(f'=== enrich_receipts_with_tier · {sport} · {datetime.now().isoformat()} '
          f'{"(DRY)" if dry_run else "(APPLY)"} ===')

    # 1) Load all source props for the sport, index by join key
    print(f'  loading source props from {prop_tbl}...')
    prop_url = (f'{SB}/rest/v1/{prop_tbl}?select=id,game_date,player_name,'
                f'prop_type,prop_line,direction,tier,conviction,'
                f'book_over_odds,book_under_odds')
    props_by_key: dict = {}
    n_props = 0
    for p in paged(prop_url, page_size=2000):
        key = (
            p.get('game_date'),
            p.get('player_name'),
            p.get('prop_type'),
            p.get('prop_line'),
            (p.get('direction') or '').lower(),
        )
        # If duplicates exist, prefer non-SKIP tier (more accurate for user-visibility)
        if key in props_by_key:
            existing = props_by_key[key]
            if existing.get('tier') == 'SKIP' and p.get('tier') != 'SKIP':
                props_by_key[key] = p
        else:
            props_by_key[key] = p
        n_props += 1
    print(f'  indexed {n_props} props → {len(props_by_key)} unique keys')

    # 2) Iterate receipts missing tier, look up source, PATCH
    receipt_url = (f'{SB}/rest/v1/public_receipts?sport=eq.{sport}'
                   f'&surface=eq.prop_jerry&tier=is.null'
                   f'&select=id,game_date,player_name,prop_type,pick_line,pick_side')
    patched = 0
    unmatched = 0
    scanned = 0
    batch: list = []
    for r in paged(receipt_url):
        scanned += 1
        key = (
            r.get('game_date'),
            r.get('player_name'),
            r.get('prop_type'),
            r.get('pick_line'),
            (r.get('pick_side') or '').lower(),
        )
        source = props_by_key.get(key)
        if not source:
            unmatched += 1
            continue
        direction = (r.get('pick_side') or '').lower()
        odds = source.get('book_over_odds') if direction == 'over' else source.get('book_under_odds')
        try:
            odds_int = int(odds) if odds is not None else None
        except (TypeError, ValueError):
            odds_int = None
        payload = {
            'tier': source.get('tier'),
            'conviction': source.get('conviction'),
            'pick_odds': odds_int,
        }
        if dry_run:
            patched += 1
            if scanned <= 5:
                print(f'    [DRY] receipt id={r["id"]} ({r["player_name"]} {r["prop_type"]} '
                      f'{r["pick_line"]} {direction}): tier={payload["tier"]} '
                      f'conv={payload["conviction"]} odds={payload["pick_odds"]}')
            continue
        # PATCH — trigger allows NULL → value transition
        pr = requests.patch(f'{SB}/rest/v1/public_receipts?id=eq.{r["id"]}',
                            headers=H_W, json=payload, timeout=15)
        if pr.status_code in (200, 201, 204):
            patched += 1
        else:
            print(f'  ✗ id={r["id"]}: {pr.status_code} {pr.text[:120]}')
        if patched % 500 == 0 and patched > 0:
            print(f'  scanned={scanned}  patched={patched}  unmatched={unmatched} ...')

    print(f'\n=== done · scanned={scanned}  patched={patched}  unmatched={unmatched} ===')
    if unmatched > 0:
        print(f'  ({unmatched} receipts had no matching source prop — likely alt-line dropped or ID drift)')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB', choices=['MLB', 'NFL'])
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(args.sport, args.dry_run)


if __name__ == '__main__':
    main()
