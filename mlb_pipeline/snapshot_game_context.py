"""Immutable daily snapshot of any sport's game_context.

WHY THIS EXISTS
mlb_game_context holds ~2 weeks. April-August 2026 is gone: asked what
the engine thought about a game on 2026-06-20, there is no answer — no
tier, no conviction, not one model prediction. The score survives in
mlb_game_results; the reasoning does not.

That is the reason there is no self-calibration. Not a missing feature —
you cannot calibrate against deleted data. This script is the fix, and
every day it does not run is another day that cannot be recovered.

DESIGN
  * ONE table for every sport (game_context_snapshots), not six.
  * The whole context row is stored as jsonb, so a new column in any
    sport is captured with no migration and no field list to drift.
  * WRITE-ONCE per (sport, game_id, snapshot_date) via
    ignore-duplicates: the FIRST capture of a day wins. The older MLB
    snapshotter used merge-duplicates, so an afternoon re-run overwrote
    the morning state — that records the last write, not what was
    published. A snapshot that can be rewritten is not evidence.

    Consequence worth stating plainly: run this EARLY, right after picks
    are published. A late first-run captures a late state.

    --force-refresh overwrites an existing same-day snapshot. It exists
    for repairing a known-bad capture and should be rare.

    python snapshot_game_context.py --sport MLB
    python snapshot_game_context.py --sport ALL
    python snapshot_game_context.py --sport NFL --date 2026-09-20
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8')
_ENV = Path(__file__).parent / '.env'
if _ENV.exists():
    for _l in _ENV.read_text().splitlines():
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if not SB or not KEY:
    print('SUPABASE_URL / SUPABASE_KEY not set — refusing to run.')
    sys.exit(1)
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

CTX_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NBA':   'nba_game_context',
    'NHL':   'nhl_game_context',
    'NCAAB': 'ncaab_game_context',
}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def snapshot(sport: str, game_date: str, force: bool = False,
             dry_run: bool = False) -> int:
    tbl = CTX_TABLE.get(sport.upper())
    if not tbl:
        print(f'  {sport}: no context table mapped'); return 0

    rows, off = [], 0
    while off < 20000:
        r = requests.get(f'{SB}/rest/v1/{tbl}',
                         params={'select': '*', 'game_date': f'eq.{game_date}',
                                 'limit': '1000', 'offset': str(off)},
                         headers=H, timeout=60)
        if r.status_code != 200:
            # Loud: a silent empty here is exactly how history was lost.
            print(f'  ⚠ {sport}: fetch failed {r.status_code} '
                  f'{r.text[:160]}')
            return 0
        b = r.json()
        if not b:
            break
        rows += b
        off += 1000
        if len(b) < 1000:
            break
    if not rows:
        print(f'  {sport} {game_date}: 0 context rows (no slate?)')
        return 0

    payload = []
    for row in rows:
        gid = row.get('game_id')
        if not gid:
            continue
        payload.append({
            'sport': sport.upper(),
            'game_id': str(gid),
            'game_date': row.get('game_date'),
            'snapshot_date': game_date,
            'context': row,
        })

    if dry_run:
        print(f'  {sport} {game_date}: would snapshot {len(payload)} rows '
              f'({len(rows[0])} columns each)')
        return len(payload)

    # ignore-duplicates => first capture of the day wins.
    pref = ('resolution=merge-duplicates' if force
            else 'resolution=ignore-duplicates')
    hw = {**H, 'Content-Type': 'application/json',
          'Prefer': f'{pref},return=minimal'}
    written = 0
    for i in range(0, len(payload), 100):
        chunk = payload[i:i + 100]
        w = requests.post(
            f'{SB}/rest/v1/game_context_snapshots'
            f'?on_conflict=sport,game_id,snapshot_date',
            headers=hw, json=chunk, timeout=60)
        if w.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ {sport} upsert {w.status_code}: {w.text[:220]}')
    print(f'  {sport} {game_date}: {written}/{len(payload)} rows snapshotted '
          f'({len(rows[0])} columns each)'
          + ('  [FORCED OVERWRITE]' if force else ''))
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='ALL')
    ap.add_argument('--date', default=None, help='default: today ET')
    ap.add_argument('--days', type=int, default=1,
                    help='also snapshot N-1 prior days (catch-up)')
    ap.add_argument('--force-refresh', action='store_true',
                    help='overwrite an existing same-day snapshot (rare)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    base = args.date or _today_et()
    sports = (list(CTX_TABLE) if args.sport.upper() == 'ALL'
              else [args.sport.upper()])
    print(f'=== snapshot_game_context · {base} · {"/".join(sports)}'
          f'{" [DRY]" if args.dry_run else ""} ===')

    total = 0
    for d_off in range(args.days):
        d = (datetime.fromisoformat(base) - timedelta(days=d_off)).date().isoformat()
        for sp in sports:
            total += snapshot(sp, d, force=args.force_refresh,
                              dry_run=args.dry_run)
    print(f'\n{"[DRY] would write" if args.dry_run else "wrote"} {total} rows')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
