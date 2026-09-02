"""Split dissent snapshot rollup (2026-09-02).

Reads public_splits_v2 (per-source money%/bets% per game/market/side),
computes per-source "sharp side" (money% > bets% by margin), rolls up
into per-game per-market agreement tag. Writes to split_dissent_snapshots.

Powers Vault Match dissent patterns (MAJ_when_CZ_dissents +16pp winner
per project_dissent_audit_822).

Agreement tags:
  TRIPLE     — 3+ sources agree on sharp side
  MAJ_2/3    — 2 sources agree, 1 dissents (record dissenter name)
  SPLIT_1v1  — 2 sources disagree, no majority
  SOLO       — 1 source only (unverifiable)

USAGE:
    python compute_split_dissent_rollup.py                # all sports
    python compute_split_dissent_rollup.py --sport MLB
    python compute_split_dissent_rollup.py --dry-run
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
H_READ  = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

LOOKBACK_DAYS = 90
SHARP_MARGIN = 5.0  # money_pct must exceed bets_pct by this to count as sharp


def _fetch_splits(sport: str, days: int) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out = []
    off = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/public_splits_v2',
            headers=H_READ,
            params={
                'sport': f'eq.{sport}',
                'snapshot_ts': f'gte.{cutoff}',
                'select': 'game_id,market,side,source,metric,value,snapshot_ts',
                'limit': '1000', 'offset': str(off),
            }, timeout=30,
        )
        if r.status_code != 200: break
        b = r.json() if isinstance(r.json(), list) else []
        if not b: break
        out.extend(b)
        if len(b) < 1000: break
        off += 1000
    return out


def compute_dissent(rows: list) -> list:
    """Group rows by (game_id, market) → per source sharp side → agreement tag."""
    # Key: (game_id, market, source, side) → {metric: value}
    per_source = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        gid = r.get('game_id'); mkt = r.get('market'); src = r.get('source')
        side = r.get('side'); metric = r.get('metric'); val = r.get('value')
        if not (gid and mkt and src and side and metric): continue
        per_source[(gid, mkt)][src][f'{side}:{metric}'] = val

    # For each (game, market): compute each source's sharp side, then aggregate
    snapshots = []
    for (gid, mkt), sources in per_source.items():
        # Skip totals markets — need 'OVER'/'UNDER' side handling separately
        # For MVP: focus on ml + rl where sides are HOME/AWAY
        side_labels = ['HOME', 'AWAY']
        if mkt == 'total':
            side_labels = ['OVER', 'UNDER']

        source_sharp_sides = {}  # source → sharp_side
        for src, metrics_map in sources.items():
            best_side = None
            best_diff = 0
            for side in side_labels:
                money = metrics_map.get(f'{side}:money_pct')
                bets = metrics_map.get(f'{side}:bets_pct')
                if money is None or bets is None: continue
                diff = money - bets
                if diff >= SHARP_MARGIN and diff > best_diff:
                    best_diff = diff
                    best_side = side
            if best_side:
                source_sharp_sides[src] = best_side

        if not source_sharp_sides: continue
        n = len(source_sharp_sides)

        # Vote per side
        from collections import Counter
        side_votes = Counter(source_sharp_sides.values())
        majority_side, majority_count = side_votes.most_common(1)[0]

        agreement = 'SOLO'
        dissenter = None
        if n == 1:
            agreement = 'SOLO'
        elif n == 2:
            agreement = 'MAJ_2/3' if majority_count == 2 else 'SPLIT_1v1'
            if agreement == 'MAJ_2/3':
                # Both agree — no dissenter, tag as TRIPLE-like consensus at n=2
                agreement = 'MAJ_2/3'
        elif n >= 3:
            if majority_count >= 3:
                agreement = 'TRIPLE'
            elif majority_count == 2:
                agreement = 'MAJ_2/3'
                # Identify the dissenting source
                for src, side in source_sharp_sides.items():
                    if side != majority_side:
                        dissenter = src
                        break

        # Determine sport from first row (they're all same sport in this batch)
        # Actually — pass sport as arg instead
        snapshots.append({
            'game_id': gid,
            'market': mkt,
            'agreement': agreement,
            'dissenter': dissenter,
            'majority_side': majority_side,
            'sources_present': list(source_sharp_sides.keys()),
            'n_sources': n,
        })
    return snapshots


def upsert_snapshots(sport: str, snapshots: list, dry_run: bool = False) -> int:
    if not snapshots: return 0
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [{**s, 'sport': sport, 'snapshot_ts': now_iso, 'last_computed_at': now_iso}
            for s in snapshots]
    if dry_run:
        from collections import Counter
        c = Counter(s['agreement'] for s in snapshots)
        print(f'  [DRY] {sport} agreement counts:', dict(c))
        for r in rows[:8]:
            print(f'    {r["game_id"][:12]} {r["market"]:6} {r["agreement"]:12} maj={r.get("majority_side")} diss={r.get("dissenter") or "-"} src={r.get("sources_present")}')
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/split_dissent_snapshots?on_conflict=sport,game_id,market',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(sport_filter: Optional[str] = None, dry_run: bool = False):
    print('=== split_dissent_snapshots rollup ===')
    sports = [sport_filter] if sport_filter else ['MLB', 'NFL', 'NCAAF', 'NBA', 'NCAAB', 'NHL']
    total = 0
    for sport in sports:
        rows = _fetch_splits(sport, LOOKBACK_DAYS)
        if not rows:
            print(f'  {sport}: no public_splits_v2 rows in {LOOKBACK_DAYS}d'); continue
        snapshots = compute_dissent(rows)
        if not snapshots:
            print(f'  {sport}: {len(rows)} rows -> 0 snapshots (need multi-source coverage)'); continue
        n = upsert_snapshots(sport, snapshots, dry_run)
        print(f'  {sport}: {len(rows)} split rows -> {n} snapshots')
        total += n
    verb = '[DRY]' if dry_run else 'wrote'
    print(f'\n  {verb} {total} total snapshots')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=['MLB','NFL','NCAAF','NBA','NCAAB','NHL'])
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(sport_filter=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
