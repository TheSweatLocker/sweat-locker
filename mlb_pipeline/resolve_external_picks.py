"""External picks resolver (2026-08-01).

Grades every external_picks row where result is null by joining with
mlb_game_results / nba_game_results / etc. on game_id.

Handles surfaces: ml, spread, total. Sport-universal.

Fills the gap: 981 external picks in DB but only ~20% graded.
Root cause: no scheduled resolver. This fixes that with a one-shot
backfill + wires into the pipeline for daily grading.

Usage:
    python resolve_external_picks.py [--date YYYY-MM-DD] [--dry-run]
    python resolve_external_picks.py --backfill  # grade all null-result rows
"""
import argparse, os, sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

RESULTS_TABLE = {
    'MLB': 'mlb_game_results',
    'NBA': 'nba_game_results',
    'NFL': 'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
}


def grade_row(row: dict, res: dict) -> tuple[str | None, float | None]:
    """Return (result 'W'/'L'/'PUSH', actual_value)."""
    hs, as_ = res.get('home_score'), res.get('away_score')
    if hs is None or as_ is None: return None, None
    surface = (row.get('surface') or '').lower()
    side = (row.get('pick_side') or '').upper()
    line = row.get('pick_line')

    if surface == 'ml':
        if hs == as_: return 'PUSH', 0
        if side == 'HOME':
            return ('W' if hs > as_ else 'L'), (hs - as_)
        if side == 'AWAY':
            return ('W' if as_ > hs else 'L'), (as_ - hs)
    elif surface == 'spread':
        if line is None: return None, None
        try: line = float(line)
        except (ValueError, TypeError): return None, None
        if side == 'HOME':
            margin = (hs - as_) + line
        elif side == 'AWAY':
            margin = (as_ - hs) + line
        else: return None, None
        if margin == 0: return 'PUSH', 0
        return ('W' if margin > 0 else 'L'), margin
    elif surface == 'total':
        if line is None:
            # Try to parse from raw_text — some sources put line in the text
            txt = str(row.get('raw_text') or '')
            import re
            m = re.search(r'(\d+\.\d+|\d+)', txt)
            if m:
                try: line = float(m.group(1))
                except: return None, None
            else: return None, None
        try: line = float(line)
        except (ValueError, TypeError): return None, None
        total = hs + as_
        if total == line: return 'PUSH', 0
        if side in ('OVER', 'O'):
            return ('W' if total > line else 'L'), (total - line)
        if side in ('UNDER', 'U'):
            return ('W' if total < line else 'L'), (line - total)
    return None, None


def resolve(game_date: str | None = None, backfill: bool = False,
            dry_run: bool = False, sport: str | None = None) -> None:
    # Pull rows that need grading
    params = {'result': 'is.null', 'select': '*', 'limit': '2000'}
    if game_date and not backfill:
        params['game_date'] = f'eq.{game_date}'
    if sport:
        params['sport'] = f'eq.{sport}'
    r = requests.get(f'{SB}/rest/v1/external_picks',
                     headers=H_READ, params=params, timeout=30).json()
    if not isinstance(r, list):
        print(f'  ⚠ fetch failed: {r}'); return
    print(f'  {len(r)} external_picks to grade')
    if not r: return

    # Group by sport → pull all result rows for that sport in-window
    by_sport: dict = {}
    for row in r:
        s = row.get('sport') or 'MLB'
        by_sport.setdefault(s, []).append(row)

    graded = skipped = 0
    for s, rows in by_sport.items():
        table = RESULTS_TABLE.get(s)
        if not table:
            print(f'  ⚠ {s}: no results table registered'); continue
        # Load unique game_ids
        game_ids = sorted({row['game_id'] for row in rows if row.get('game_id')})
        if not game_ids: continue
        # PostgREST IN filter — chunk if huge
        by_gid: dict = {}
        for i in range(0, len(game_ids), 100):
            chunk = game_ids[i:i+100]
            in_clause = ','.join(f'\"{g}\"' for g in chunk)
            gr = requests.get(f'{SB}/rest/v1/{table}',
                              headers=H_READ,
                              params={'game_id': f'in.({in_clause})',
                                      'select': 'game_id,home_score,away_score',
                                      'limit': '500'}, timeout=15).json()
            for x in (gr if isinstance(gr, list) else []):
                by_gid[x['game_id']] = x

        for row in rows:
            res = by_gid.get(row['game_id'])
            if not res or res.get('home_score') is None:
                skipped += 1; continue
            outcome, actual = grade_row(row, res)
            if outcome is None:
                skipped += 1; continue
            if dry_run:
                graded += 1; continue
            payload = {
                'result': outcome,
                'actual_value': float(actual) if actual is not None else None,
                'resolved_at': datetime.now(timezone.utc).isoformat(),
            }
            pu = requests.patch(f'{SB}/rest/v1/external_picks?id=eq.{row["id"]}',
                                headers=H_WRITE, json=payload, timeout=10)
            if pu.status_code in (200, 204):
                graded += 1
            else:
                skipped += 1

    print(f'\n  graded {graded} · skipped {skipped}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--sport')
    p.add_argument('--backfill', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    resolve(game_date=args.date, backfill=args.backfill,
            dry_run=args.dry_run, sport=args.sport)
