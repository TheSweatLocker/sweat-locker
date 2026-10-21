#!/usr/bin/env python3
"""Collapse duplicate prop rows to ONE live row per (date, player, market, side).

WHY THIS EXISTS
───────────────
Both nhl_generate_props and nba_generate_props upsert with

    on_conflict=game_date,player_name,prop_type,direction,prop_line

The line is part of the conflict key. So when a book moves the number between
runs there is no conflict and the upsert INSERTS A NEW ROW. The generators
also write props for games several days out, so a slate gets written once days
early and again on game day, at a different line, and both rows persist.

Measured on 2026-10-21:

    nhl_pipeline_props   62,007 rows  <- the largest table in the system
    nba_pipeline_props    3,222 rows for ONE day's games, of which 1,442 were
                          created on 10-19 and 1,780 on 10-21
    worst NBA slot: one player/market/side holding 16 rows, 16 distinct lines

That is the disk-IO problem Supabase flagged. It is also, and more
importantly, WHY NHL AND NBA PROPS NEVER SCORE:

    the workflow order is generate -> backfill_prop_lookback -> scorer

The backfill writes player_l5_hit_count / player_l10_hit_count /
player_season_hit_pct onto the rows that exist when it runs. A later generator
run creates FRESH rows at a new line, with those columns NULL, and the
enriched rows become orphans. The scorer then reads blank rows, so of NHL's 15
enabled signals only the odds-juice one can fire, the 3+ signal depth gate is
never met, and tier stays COVERAGE with conviction 0 forever. Verified: L5/L10
were fed on 0 of 420 NHL and 0 of 3,222 NBA rows, yet running the backfill by
hand immediately fed 418 of 420. The backfill was never broken. Its output was
being thrown away.

WHY COLLAPSE INSTEAD OF A UNIQUE INDEX
──────────────────────────────────────
Dropping prop_line from the conflict key needs a matching unique index, and a
unique index must hold for every row in the table -- which would mean deleting
roughly 30,000 HISTORICAL NHL rows. 56,569 of the 62,007 NHL rows carry a
`result`: that is graded history feeding the records surfaces. Records do not
get rewritten to tidy up a key. So this collapses only rows that are still
live, and a partial unique index is not an option either because ON CONFLICT
cannot infer a partial index's predicate on a plain INSERT.

SAFETY RULES, all enforced below
────────────────────────────────
  * only rows with result IS NULL are ever deleted. A graded row is history.
  * only rows whose tier is COVERAGE (or null) are deleted. Nothing that was
    ever LEAN/STRONG/PRIME is touched -- verified 0 such rows exist in either
    table today, but the gate stays so that stops being load-bearing.
  * within a slot the NEWEST row by created_at is kept, because that is the
    one carrying the current line and the price the generator just wrote.
  * dry-run by default. --apply is required to delete anything.
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, '.env'))
load_dotenv(os.path.join(os.path.dirname(HERE), '.env'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_SERVICE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = dict(H, **{'Content-Type': 'application/json',
                 'Prefer': 'return=minimal'})

TABLES = {'NHL': 'nhl_pipeline_props', 'NBA': 'nba_pipeline_props'}

# Slot identity: everything that defines the bet EXCEPT the line. Two rows
# that agree on these four are the same prop quoted at two different numbers,
# and only the current number is live.
SLOT = ('game_date', 'player_name', 'prop_type', 'direction')

SELECT = ('id,game_date,player_name,prop_type,direction,prop_line,result,'
          'tier,created_at')


def _fetch(table: str, params: dict) -> list:
    """Paginated read. Never trusts a single page -- a bare GET stops at the
    PostgREST row cap and a partial read here would delete the wrong rows."""
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                         timeout=60)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{table} read {r.status_code}: {r.text[:160]}')
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        out += batch
        if len(batch) < 1000:
            break
        off += 1000
    return out


def plan(table: str, start: str, end: str) -> tuple[list, dict]:
    """Return (ids_to_delete, stats) for game_date in [start, end]."""
    rows = _fetch(table, {'select': SELECT,
                          'game_date': f'gte.{start}',
                          'result': 'is.null'})
    rows = [r for r in rows if (r.get('game_date') or '') <= end]

    by_slot = defaultdict(list)
    for r in rows:
        by_slot[tuple(r.get(k) for k in SLOT)].append(r)

    doomed, slots_with_dupes = [], 0
    for slot, group in by_slot.items():
        if len(group) < 2:
            continue
        # Defensive: never delete a row that was ever published at a real tier.
        safe = [g for g in group
                if (g.get('tier') or 'COVERAGE') == 'COVERAGE'
                and g.get('result') is None]
        if len(safe) < 2:
            continue
        safe.sort(key=lambda g: (str(g.get('created_at') or ''), g['id']))
        keep = safe[-1]                      # newest == current line + price
        for g in safe:
            if g['id'] != keep['id']:
                doomed.append(g['id'])
        slots_with_dupes += 1

    stats = {'live_rows': len(rows), 'slots': len(by_slot),
             'slots_with_dupes': slots_with_dupes, 'to_delete': len(doomed)}
    return doomed, stats


def delete_ids(table: str, ids: list, batch: int = 100) -> int:
    done = 0
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        lst = ','.join(str(x) for x in chunk)
        r = requests.delete(f'{SB}/rest/v1/{table}', headers=H_W,
                            params={'id': f'in.({lst})'}, timeout=60)
        if r.status_code not in (200, 204):
            print(f'    delete {r.status_code}: {r.text[:160]}')
            break
        done += len(chunk)
    return done


def run(sport: str, start: str, end: str, apply: bool) -> dict:
    table = TABLES[sport]
    doomed, st = plan(table, start, end)
    print(f'  {sport} {table}  {start}..{end}')
    print(f'    live (ungraded) rows   : {st["live_rows"]}')
    print(f'    distinct slots         : {st["slots"]}')
    print(f'    slots with duplicates  : {st["slots_with_dupes"]}')
    print(f'    superseded rows        : {st["to_delete"]}')
    if not doomed:
        print('    nothing to collapse')
        return st
    if not apply:
        print('    DRY RUN — pass --apply to delete')
        return st
    n = delete_ids(table, doomed)
    print(f'    deleted {n}')
    st['deleted'] = n
    return st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(TABLES), required=True)
    ap.add_argument('--date', help='single game_date (default: today ET)')
    ap.add_argument('--through', type=int, default=7,
                    help='also sweep this many days FORWARD of --date, '
                         'because the generators write future slates early '
                         'and that is where duplicates accumulate')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not SB or not KEY:
        print('FAIL: SUPABASE_URL / service key not in env')
        return 2
    start = a.date or date.today().isoformat()
    end = (date.fromisoformat(start) + timedelta(days=a.through)).isoformat()
    print(f'=== prop slot reconcile · {a.sport} ===')
    run(a.sport, start, end, a.apply)
    return 0


if __name__ == '__main__':
    sys.exit(main())
