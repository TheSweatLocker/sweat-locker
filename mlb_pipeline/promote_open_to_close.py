#!/usr/bin/env python3
"""promote_open_to_close — ensure close_* market fields never stay NULL
when open_* has values for today's games.

Root cause (2026-08-28): game_context.py writes close_* fields only when
`is_open_run=False`. Games that only ever get the "open" run (early
morning) or that have `is_open_run=True` persistently keep close_*=NULL
even though open_* has real values. Downstream consumers (app display,
CLV computation, freeze_closing_lines) require close_* to be populated
to render / grade. 8/28 slate had 3 games (SEA/TOR, BAL/OAK, ARI/SF)
stuck at open-only.

This script runs late in every sport pipeline (after game_context, odds
pull, and any close-freeze steps). For any row on today's date where
close_* is NULL but open_* has a value, promotes open→close in place.
Idempotent: rows with close_* already set are skipped.

Sport-universal via SPORT_CONFIG.

CLI:
  python promote_open_to_close.py                    # today, all sports
  python promote_open_to_close.py --sport MLB
  python promote_open_to_close.py --date 2026-08-28
  python promote_open_to_close.py --dry-run
"""

from __future__ import annotations
import argparse, os, sys, datetime as dt
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests
SB  = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
H   = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW  = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# open_col → close_col per sport. Only MLB uses home_ml_close naming;
# NFL/NCAAF/NCAAB/NHL use close_home_ml / close_away_ml.
SPORT_CONFIG = {
    'MLB': {
        'table': 'mlb_game_context',
        'pairs': [
            ('open_total',    'close_total'),
            ('open_spread',   'close_spread'),
            ('home_ml_open',  'home_ml_close'),
            ('away_ml_open',  'away_ml_close'),
        ],
    },
    'NFL': {
        'table': 'nfl_game_context',
        'pairs': [
            ('open_total',   'close_total'),
            ('open_spread',  'close_spread'),
            ('open_home_ml', 'close_home_ml'),
            ('open_away_ml', 'close_away_ml'),
        ],
    },
    'NCAAF': {
        'table': 'ncaaf_game_context',
        'pairs': [
            ('open_total',   'close_total'),
            ('open_spread',  'close_spread'),
            ('open_home_ml', 'close_home_ml'),
            ('open_away_ml', 'close_away_ml'),
        ],
    },
    'NCAAB': {
        'table': 'ncaab_game_context',
        'pairs': [
            ('open_total',   'close_total'),
            ('open_spread',  'close_spread'),
            ('open_home_ml', 'close_home_ml'),
            ('open_away_ml', 'close_away_ml'),
        ],
    },
    'NHL': {
        'table': 'nhl_game_context',
        'pairs': [
            ('open_total',   'close_total'),
            ('open_spread',  'close_spread'),
            ('open_home_ml', 'close_home_ml'),
            ('open_away_ml', 'close_away_ml'),
        ],
    },
}


def promote_sport(sport: str, gd: str, dry_run: bool = False) -> tuple[int, int]:
    """Return (checked, patched)."""
    cfg = SPORT_CONFIG.get(sport)
    if not cfg: return (0, 0)
    cols = ['game_id'] + [c for pair in cfg['pairs'] for c in pair]
    select_cols = ','.join(cols)
    r = requests.get(
        f'{SB}/rest/v1/{cfg["table"]}?game_date=eq.{gd}&select={select_cols}',
        headers=H, timeout=30,
    )
    if r.status_code != 200:
        print(f'  {sport}: ctx fetch failed {r.status_code}')
        return (0, 0)
    rows = r.json() or []
    checked = patched = 0
    for row in rows:
        checked += 1
        payload = {}
        for open_col, close_col in cfg['pairs']:
            if row.get(close_col) is None and row.get(open_col) is not None:
                payload[close_col] = row[open_col]
        if not payload: continue
        if dry_run:
            patched += 1
            print(f'  DRY {sport}/{row["game_id"][:12]}...: promote {list(payload.keys())}')
            continue
        pr = requests.patch(
            f'{SB}/rest/v1/{cfg["table"]}?game_id=eq.{row["game_id"]}',
            headers=HW, json=payload, timeout=15,
        )
        if pr.status_code in (200, 204):
            patched += 1
        else:
            print(f'  {sport} patch {row["game_id"][:12]}: {pr.status_code}')
    return (checked, patched)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=['ALL'] + list(SPORT_CONFIG),
                    default='ALL')
    ap.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    gd = args.date or (dt.datetime.utcnow() - dt.timedelta(hours=4)).date().isoformat()
    sports = list(SPORT_CONFIG) if args.sport == 'ALL' else [args.sport]

    print(f'=== promote_open_to_close · {gd} · {"/".join(sports)}{" [DRY]" if args.dry_run else ""} ===')
    total_c = total_p = 0
    for sp in sports:
        c, p = promote_sport(sp, gd, dry_run=args.dry_run)
        if c or p:
            print(f'  {sp}: checked={c}  promoted={p}')
        total_c += c; total_p += p
    print(f'DONE — {total_p}/{total_c} rows promoted')


if __name__ == '__main__':
    main()
