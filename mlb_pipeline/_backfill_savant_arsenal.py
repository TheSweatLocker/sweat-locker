"""One-time backfill: apply Baseball Savant arsenal stats to the 627
pitcher_stats rows that were NULLed by the 7/25 DQ cleanup.

Fetches per-pitcher whiff% + hard_hit% from Savant's pitch-arsenal-stats
CSV, then PATCHes any mlb_pitcher_stats row where the field is NULL.

Doesn't touch rows that already have data (Savant arsenal doesn't
have EVERY pitcher — ~562 have arsenal data; remaining will stay NULL
until they qualify for Savant tracking or FanGraphs picks them up).

Usage:
    python _backfill_savant_arsenal.py --dry-run
    python _backfill_savant_arsenal.py
"""
import argparse, os, requests, sys, time
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
env = Path('.env').read_text()
for line in env.split('\n'):
    if '=' in line and not line.startswith('#'):
        k,v = line.split('=',1); os.environ[k.strip()] = v.strip()
url = os.environ['SUPABASE_URL']; key = os.environ['SUPABASE_KEY']
h = {'apikey': key, 'Authorization': f'Bearer {key}'}
hw = {**h, 'Content-Type':'application/json','Prefer':'return=minimal'}

from pitcher_stats import fetch_savant_arsenal_stats


def run(dry_run: bool = False) -> None:
    # 1. Pull Savant arsenal data (2026 + 2025 fallback)
    arsenal_2026 = fetch_savant_arsenal_stats(year=2026)
    time.sleep(1)
    arsenal_2025 = fetch_savant_arsenal_stats(year=2025)
    print(f'\n2026 arsenal: {len(arsenal_2026)}  |  2025 arsenal: {len(arsenal_2025)}\n')

    # 2. Pull all pitcher_stats rows that need arsenal data
    rows = []; off = 0
    while True:
        r = requests.get(
            f'{url}/rest/v1/mlb_pitcher_stats'
            f'?select=id,player_name,whiff_rate,hard_hit_pct'
            f'&or=(whiff_rate.is.null,hard_hit_pct.is.null)'
            f'&limit=1000&offset={off}',
            headers=h, timeout=30,
        )
        data = r.json()
        if not isinstance(data, list) or not data: break
        rows.extend(data)
        if len(data) < 1000: break
        off += 1000
    print(f'Rows needing arsenal supplement: {len(rows)}\n')

    # 3. For each row, try 2026 arsenal first, then 2025 fallback
    updated = 0; still_missing = 0; failed = 0
    updates_by_source = {'2026': 0, '2025': 0, 'none': 0}
    for r in rows:
        name = r['player_name']
        key_lower = name.lower()
        last_lower = name.split(' ')[-1].lower()
        arsenal = arsenal_2026.get(key_lower) or arsenal_2026.get(last_lower)
        source_year = '2026'
        if not arsenal:
            arsenal = arsenal_2025.get(key_lower) or arsenal_2025.get(last_lower)
            source_year = '2025'
        if not arsenal:
            still_missing += 1
            updates_by_source['none'] += 1
            continue

        # Build patch payload — only null fields
        payload = {}
        if r.get('whiff_rate') is None and arsenal.get('whiff_rate') is not None:
            payload['whiff_rate'] = arsenal['whiff_rate']
        if r.get('hard_hit_pct') is None and arsenal.get('hard_hit_pct') is not None:
            payload['hard_hit_pct'] = arsenal['hard_hit_pct']
        if not payload:
            continue

        if dry_run:
            updated += 1
            updates_by_source[source_year] += 1
            if updated <= 8:
                print(f'  [DRY] {name}: {payload} (from {source_year})')
            continue

        resp = requests.patch(
            f'{url}/rest/v1/mlb_pitcher_stats?id=eq.{r["id"]}',
            headers=hw, json=payload,
        )
        if resp.status_code < 300:
            updated += 1
            updates_by_source[source_year] += 1
        else:
            failed += 1

    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}Applied arsenal to {updated} rows')
    print(f'  from 2026 Savant: {updates_by_source["2026"]}')
    print(f'  from 2025 fallback: {updates_by_source["2025"]}')
    print(f'  still missing (no Savant data): {still_missing}')
    if failed:
        print(f'  ⚠ failed patches: {failed}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
