"""One-time cleanup: null out the hardcoded-default rows in mlb_pitcher_stats.

Per _audit_pitcher_stats_dq (7/25), 627 of 798 rows had identical stub
defaults across 5 Statcast columns:
  whiff_rate=10.0, hard_hit_pct=35.0, barrel_pct=6.0,
  avg_fastball_velo=93.0, lob_pct=72.0

Root cause fixed in pitcher_stats.py (defaults changed to None). But
existing bad rows won't get overwritten by next cron because
resolution=merge-duplicates only replaces when the new value differs.
This script explicitly nulls the polluted rows so the next pipeline
run can populate them correctly if Savant data becomes available.

Also flags pitchers where we have partial Statcast (e.g., whiff is a
real decimal but hard_hit is default 35.0) as inconsistent.

Usage:
    python _cleanup_pitcher_stats_defaults.py --dry-run   # preview
    python _cleanup_pitcher_stats_defaults.py            # apply
"""
import argparse, os, requests, sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
env = Path('.env').read_text()
for line in env.split('\n'):
    if '=' in line and not line.startswith('#'):
        k,v = line.split('=',1); os.environ[k.strip()] = v.strip()
url = os.environ['SUPABASE_URL']; key = os.environ['SUPABASE_KEY']
h = {'apikey': key, 'Authorization': f'Bearer {key}'}
hw = {**h, 'Content-Type':'application/json','Prefer':'return=minimal'}

DEFAULTS = {
    'whiff_rate': 10.0,
    'hard_hit_pct': 35.0,
    'barrel_pct': 6.0,
    'avg_fastball_velo': 93.0,
    'lob_pct': 72.0,
}

def is_default_val(field, val):
    if val is None: return False
    try:
        return abs(float(val) - DEFAULTS[field]) < 0.01
    except (TypeError, ValueError):
        return False


def run(dry_run: bool = False) -> None:
    rows = []; off = 0
    while True:
        r = requests.get(f'{url}/rest/v1/mlb_pitcher_stats?select=id,player_name,team,whiff_rate,hard_hit_pct,barrel_pct,avg_fastball_velo,lob_pct&limit=1000&offset={off}', headers=h, timeout=30)
        data = r.json()
        if not isinstance(data, list) or not data: break
        rows.extend(data)
        if len(data) < 1000: break
        off += 1000
    print(f'Loaded {len(rows)} pitcher rows')

    # Bucket rows by pattern
    all_defaults = []      # all 5 fields at default → most likely stub
    partial_defaults = []  # some defaults, some real
    clean = []             # no defaults

    for r in rows:
        default_hits = [f for f in DEFAULTS if is_default_val(f, r.get(f))]
        real_hits = [f for f in DEFAULTS if r.get(f) is not None and not is_default_val(f, r.get(f))]
        if len(default_hits) == 5:
            all_defaults.append(r)
        elif default_hits:
            partial_defaults.append((r, default_hits, real_hits))
        else:
            clean.append(r)

    print(f'\n  all-5-defaults (stub rows):   {len(all_defaults)}  → will NULL these fields')
    print(f'  partial defaults (mixed):     {len(partial_defaults)}  → will NULL only default fields')
    print(f'  clean (has real Savant data): {len(clean)}\n')

    # Show sample of the "mixed" pattern for sanity
    if partial_defaults:
        print('  Sample mixed rows (first 5):')
        for r, defs, reals in partial_defaults[:5]:
            print(f"    {r['player_name']:<24}  defaults={defs}  real={reals}")
        print()

    # Apply
    if dry_run:
        print(f'[DRY] would null default fields on {len(all_defaults) + len(partial_defaults)} rows')
        return

    fixed = 0; failed = 0
    # Full stub rows: null all 5
    for r in all_defaults:
        payload = {f: None for f in DEFAULTS}
        resp = requests.patch(f'{url}/rest/v1/mlb_pitcher_stats?id=eq.{r["id"]}', headers=hw, json=payload)
        if resp.status_code < 300: fixed += 1
        else: failed += 1
    # Partial rows: null only the default-looking fields
    for r, defs, _ in partial_defaults:
        payload = {f: None for f in defs}
        resp = requests.patch(f'{url}/rest/v1/mlb_pitcher_stats?id=eq.{r["id"]}', headers=hw, json=payload)
        if resp.status_code < 300: fixed += 1
        else: failed += 1

    print(f'\n✓ Nulled default fields on {fixed} rows.  Failed: {failed}')
    print('  Next pipeline run will populate real values where available.')
    print('  Fields with no real data source will remain NULL (correct behavior).')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
