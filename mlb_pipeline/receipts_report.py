"""Side-by-side comparison: what surface_records claims vs what
public_receipts proves.

Andy directive 2026-09-18: "I want to see the record before we change
anything. Still want to inflated note down." — this script computes
both and writes to receipts_vs_current_records so Andy can eyeball the
delta before we flip the aggregator source.

Reads:
    surface_records          — current inflated app-facing numbers
    public_receipts          — truth (populated by backfill_public_receipts.py)

Writes:
    receipts_vs_current_records
        one row per (sport, surface, window_key, tier)
        columns: current_wins/losses/picks/units, receipt_wins/losses/picks/units,
                 inflation_ratio

Usage:
    python receipts_report.py --sport MLB
    python receipts_report.py --sport MLB --console       # print table, no DB write

2026-09-18: See memory/project_public_receipts_integrity_918.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
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
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

# surface_records surface names → receipts surface / tier filter
SURFACE_MAP = {
    'prop':            ('prop_jerry', None),      # legacy 'prop' surface
    'prop_prime':      ('prop_jerry', 'PRIME'),
    'prop_strong':     ('prop_jerry', 'STRONG'),
    'prop_lean':       ('prop_jerry', 'LEAN'),
    'prop_coverage':   ('prop_jerry', 'COVERAGE'),
    'potd':            ('potd', None),
    'sharp':           ('game_read', None),
    'sharp_card':      ('sharp_card', None),
    'ladder':          ('ladder', None),
    'ledger':          ('ledger', None),
    'dawg':            ('dawg', None),
}

WINDOWS = {
    'd7':  7,
    'd30': 30,
    'mtd': None,     # mtd handled separately
}


def window_start(win: str, today: date) -> date:
    if win == 'mtd':
        return today.replace(day=1)
    days = WINDOWS.get(win, 30)
    return today - timedelta(days=days)


def fetch_current(sport: str) -> list[dict]:
    r = requests.get(
        f'{SB}/rest/v1/surface_records?sport=eq.{sport}'
        f'&window_key=in.(d7,d30,mtd,lifetime)'
        f'&select=surface,window_key,wins,losses,pushes,picks_count,units_net,hit_rate',
        headers=H_R, timeout=30,
    ).json()
    return r if isinstance(r, list) else []


def fetch_receipts_rollup(sport: str, receipt_surface: str, tier_filter: str | None,
                          start_date: date, end_date: date) -> dict:
    """Roll up public_receipts for the given window into wins/losses/units."""
    url = (f'{SB}/rest/v1/public_receipts?sport=eq.{sport}'
           f'&surface=eq.{receipt_surface}'
           f'&game_date=gte.{start_date.isoformat()}'
           f'&game_date=lte.{end_date.isoformat()}'
           f'&result=not.is.null'
           f'&select=result,pick_odds,tier')
    if tier_filter:
        url += f'&tier=eq.{tier_filter}'
    r = requests.get(url, headers=H_R, timeout=30).json()
    if not isinstance(r, list):
        return {'wins': 0, 'losses': 0, 'picks': 0, 'units': 0.0}
    w = l = p = 0
    units = 0.0
    for row in r:
        res = (row.get('result') or '').upper()
        odds = row.get('pick_odds')
        stake = 1.0
        if res == 'WIN':
            w += 1
            if odds is not None:
                try:
                    o = int(odds)
                    units += (stake * 100 / abs(o)) if o < 0 else (stake * o / 100)
                except (TypeError, ValueError):
                    units += 0.909
            else:
                units += 0.909
        elif res == 'LOSS':
            l += 1
            units -= stake
        elif res in ('PUSH', 'VOID'):
            p += 1
    return {'wins': w, 'losses': l, 'pushes': p, 'picks': w + l + p, 'units': round(units, 2)}


def run(sport: str, console_only: bool = False):
    today = datetime.now(timezone.utc).date()
    print(f'=== receipts_report · {sport} · {today.isoformat()} '
          f'{"(CONSOLE)" if console_only else "(WRITING receipts_vs_current_records)"} ===\n')

    current_rows = fetch_current(sport)
    current_lookup = {(r['surface'], r['window_key']): r for r in current_rows if isinstance(r, dict)}

    write_batch = []
    print(f"{'SURFACE':<18} {'WINDOW':<8} {'CURRENT (surface_records)':<38} {'RECEIPTS (truth)':<32} {'INFLATION':<8}")
    print('-' * 108)

    for sr_surface, (receipt_surface, tier_filter) in SURFACE_MAP.items():
        for window in ['d7', 'd30', 'mtd']:
            start = window_start(window, today)
            cur = current_lookup.get((sr_surface, window)) or {}
            cur_w = cur.get('wins') or 0
            cur_l = cur.get('losses') or 0
            cur_n = cur.get('picks_count') or 0
            cur_u = cur.get('units_net') or 0
            rc = fetch_receipts_rollup(sport, receipt_surface, tier_filter, start, today)
            rc_w = rc['wins']; rc_l = rc['losses']; rc_n = rc['picks']; rc_u = rc['units']
            ratio = round(cur_n / rc_n, 2) if rc_n else None

            cur_str = f'{cur_w}-{cur_l} · n={cur_n} · {cur_u:+.1f}u'
            rc_str = f'{rc_w}-{rc_l} · n={rc_n} · {rc_u:+.1f}u'
            ratio_str = f'{ratio}x' if ratio else '—'
            print(f"{sr_surface:<18} {window:<8} {cur_str:<38} {rc_str:<32} {ratio_str}")

            write_batch.append({
                'sport': sport, 'surface': sr_surface, 'window_key': window,
                'tier': tier_filter,
                'current_wins': cur_w, 'current_losses': cur_l, 'current_picks': cur_n,
                'current_units': float(cur_u) if cur_u is not None else None,
                'receipt_wins': rc_w, 'receipt_losses': rc_l, 'receipt_picks': rc_n,
                'receipt_units': rc_u,
                'inflation_ratio': ratio,
            })
        print()

    if console_only:
        print('  (console-only — not writing receipts_vs_current_records)')
        return

    r = requests.post(
        f'{SB}/rest/v1/receipts_vs_current_records'
        f'?on_conflict=sport,surface,window_key,tier',
        headers=H_W, json=write_batch, timeout=60,
    )
    if r.status_code in (200, 201, 204):
        print(f'✓ wrote {len(write_batch)} rows to receipts_vs_current_records')
    else:
        print(f'⚠ write failed {r.status_code}: {r.text[:300]}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB', choices=['MLB', 'NFL', 'NCAAF', 'NBA', 'NHL', 'NCAAB', 'UFC'])
    p.add_argument('--console', action='store_true', help='print table only, no DB write')
    args = p.parse_args()
    run(args.sport, console_only=args.console)


if __name__ == '__main__':
    main()
