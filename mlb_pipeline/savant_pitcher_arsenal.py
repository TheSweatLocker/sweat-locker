"""Baseball Savant pitcher batted-ball + full SIERA pull (2026-08-15).

Nightly pull of pitcher-level batted-ball rates (GB%, FB%, LD%, PU%)
plus K%/BB% from Savant's custom leaderboard. Computes full SIERA and
upserts to savant_pitcher_stats.

Fixes the "simplified SIERA loses 23% to xERA" problem from
project_advanced_metrics_backtest_815 — full formula includes GB-FB-PU
interaction terms that the simplified version dropped.

DATA SOURCE
  https://baseballsavant.mlb.com/leaderboard/custom
    ?year=YYYY
    &type=pitcher
    &min=50
    &selections=k_percent,bb_percent,groundballs_percent,
                flyballs_percent,linedrives_percent,popups_percent

  ~234 rows for a mid-season pull. min=50 means pitchers with 50+
  batters faced this season. Includes both starters + high-usage
  relievers.

FULL SIERA FORMULA (FanGraphs specification)
  siera = 6.145
        - 16.986 * (K/PA)
        + 11.434 * (BB/PA)
        - 1.858  * (GB%)
        + 7.653  * (K/PA)^2
        + interaction(GB, FB, PU)
        + 10.130 * (K/PA) * (GB% - FB% - PU%)

  Where interaction:
    diff = GB% - FB% - PU%
    if diff < 0: -6.664 * diff^2
    else:        +6.664 * diff^2

Floored at 0.50 (negative SIERA is nonsensical).

CLI
  python savant_pitcher_arsenal.py                    # current season
  python savant_pitcher_arsenal.py --season 2025
  python savant_pitcher_arsenal.py --dry-run
"""
from __future__ import annotations
import argparse, io, os, sys
from datetime import datetime, timezone
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

SAVANT_URL = ('https://baseballsavant.mlb.com/leaderboard/custom'
              '?year={year}&type=pitcher&min=50'
              '&selections=k_percent,bb_percent,groundballs_percent,'
              'flyballs_percent,linedrives_percent,popups_percent'
              '&csv=true')

HEADERS = {'User-Agent': 'Mozilla/5.0 (Sweat Locker · sabermetrics)'}


def compute_siera_full(k_pct: float, bb_pct: float,
                        gb_pct: float, fb_pct: float, pu_pct: float) -> float:
    """Full SIERA formula per FanGraphs specification."""
    # Normalize: Savant returns percentages 0-100, formula expects decimals
    k  = k_pct  / 100.0 if k_pct  > 1.0 else k_pct
    bb = bb_pct / 100.0 if bb_pct > 1.0 else bb_pct
    gb = gb_pct / 100.0 if gb_pct > 1.0 else gb_pct
    fb = fb_pct / 100.0 if fb_pct > 1.0 else fb_pct
    pu = pu_pct / 100.0 if pu_pct > 1.0 else pu_pct

    diff = gb - fb - pu
    interaction = (-6.664 * (diff ** 2)) if diff < 0 else (6.664 * (diff ** 2))

    siera = (6.145
             - 16.986 * k
             + 11.434 * bb
             - 1.858  * gb
             + 7.653  * (k ** 2)
             + interaction
             + 10.130 * k * diff)
    return round(max(0.50, siera), 2)


def fetch_savant(season: int):
    """Pull Savant CSV. Returns list of dicts."""
    import pandas as pd
    url = SAVANT_URL.format(year=season)
    r = requests.get(url, headers=HEADERS, timeout=45)
    if r.status_code != 200:
        print(f'  ✗ Savant HTTP {r.status_code}')
        return []
    try:
        df = pd.read_csv(io.StringIO(r.text))
    except Exception as e:
        print(f'  ✗ CSV parse fail: {e}')
        return []
    rows = []
    for _, row in df.iterrows():
        try:
            # Savant name format: "Last, First"
            name = str(row['last_name, first_name'])
            if ',' in name:
                last, first = name.split(',', 1)
                display = f'{first.strip()} {last.strip()}'
            else:
                display = name
            rows.append({
                'mlb_id': int(row['player_id']),
                'pitcher_name': display,
                'k_pct':  float(row['k_percent']),
                'bb_pct': float(row['bb_percent']),
                'gb_pct': float(row['groundballs_percent']),
                'fb_pct': float(row['flyballs_percent']),
                'ld_pct': float(row['linedrives_percent']),
                'pu_pct': float(row['popups_percent']),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return rows


def upsert_rows(rows: list, season: int, dry_run: bool = False) -> int:
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for r in rows:
        siera = compute_siera_full(
            r['k_pct'], r['bb_pct'], r['gb_pct'], r['fb_pct'], r['pu_pct'])
        payloads.append({
            'mlb_id': r['mlb_id'],
            'pitcher_name': r['pitcher_name'],
            'season': season,
            'k_pct': r['k_pct'],
            'bb_pct': r['bb_pct'],
            'gb_pct': r['gb_pct'],
            'fb_pct': r['fb_pct'],
            'ld_pct': r['ld_pct'],
            'pu_pct': r['pu_pct'],
            'siera_full': siera,
            'updated_at': now_iso,
        })

    if dry_run:
        print(f'  [DRY] {len(payloads)} pitchers to upsert')
        for p in payloads[:5]:
            print(f'    {p["pitcher_name"]:24} K%={p["k_pct"]}  GB%={p["gb_pct"]}  '
                  f'FB%={p["fb_pct"]}  PU%={p["pu_pct"]}  SIERA={p["siera_full"]}')
        return len(payloads)

    written = 0
    for i in range(0, len(payloads), 200):
        chunk = payloads[i:i+200]
        pr = requests.post(
            f'{SB}/rest/v1/savant_pitcher_stats?on_conflict=mlb_id,season',
            headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:200]}')
    return written


def run(season: int, dry_run: bool = False) -> int:
    print(f'=== savant_pitcher_arsenal · season {season} ===')
    rows = fetch_savant(season)
    print(f'  fetched {len(rows)} pitcher-rows from Savant')
    if not rows:
        return 0
    written = upsert_rows(rows, season, dry_run=dry_run)
    print(f'  {"[DRY] " if dry_run else "✓ "}wrote {written} rows')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', type=int, default=datetime.now().year)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
