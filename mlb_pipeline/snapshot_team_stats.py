"""Append today's team_stats_rolling values to team_stats_rolling_history.

Andy 2026-09-26, approving the conclusion of study_stat_edge: the five stats
with leak-free history carry no ATS edge over 16,736 games, and the stats
that might — EPA, success rate, explosiveness, SP+ — cannot be tested at all
because team_stats_rolling is a matview holding only the current value. This
script is the thing that changes that answer next season.

Runs straight after the refresh_team_stats_rolling RPC in mlb_grade_overnight,
so it captures the values the matview was just rebuilt to.

CHANGE-ONLY. A row is appended only when a stat's (raw_value, rank) differs
from that key's most recent snapshot. A full daily copy would be 9,658
rows/day (~3.5M/year) and nearly all duplicates — football moves weekly, and
NCAAB alone is 4,523 rows that will not move until November. The as-of-date
query is unchanged either way: latest snapshot before the game.

IDEMPOTENT. Re-running on the same day upserts the same (sport, team, season,
stat_key, snapshot_date) key, so a retry or a double-scheduled run cannot
duplicate or corrupt a day.

    python snapshot_team_stats.py                # all sports
    python snapshot_team_stats.py --sport NCAAF
    python snapshot_team_stats.py --dry-run
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

HIST = 'team_stats_rolling_history'
FIELDS = ('raw_value', 'rank', 'league_size', 'direction', 'display_label', 'unit')


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def page(path: str, params: dict) -> list:
    """Paginated read — team_stats_rolling is ~9.7k rows, well past the
    1000-row PostgREST default (project_postgrest_truncation_audit_912)."""
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ⚠ read {path} -> {r.status_code}: {(r.text or "")[:200]}')
            sys.exit(2)
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _key(row: dict) -> tuple:
    return (row['sport'], row['team'], row['season'], row['stat_key'])


def _changed(cur: dict, prev: dict | None) -> bool:
    """Only raw_value and rank define a change. The label/unit/direction are
    presentation and would churn the table on a cosmetic migration without
    representing any new measurement."""
    if prev is None:
        return True
    for f in ('raw_value', 'rank'):
        a, b = cur.get(f), prev.get(f)
        if a is None and b is None:
            continue
        if a is None or b is None:
            return True
        if float(a) != float(b):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--date', default=None, help='override snapshot_date (YYYY-MM-DD)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    snap_date = args.date or _today_et()

    sel = 'sport,team,season,stat_key,raw_value,rank,league_size,direction,display_label,unit'
    cur_params = {'select': sel}
    if args.sport:
        cur_params['sport'] = f'eq.{args.sport}'
    current = page('team_stats_rolling', cur_params)

    print(f'=== snapshot_team_stats · {snap_date}'
          f'{" [DRY]" if args.dry_run else ""} ===')
    print(f'  live team_stats_rolling rows: {len(current)}')
    if not current:
        print('  nothing to snapshot.')
        return

    # Latest prior snapshot per key. Pulling only rows STRICTLY BEFORE today
    # means a same-day re-run compares against yesterday, so a value that
    # changed twice in one day still records its latest state.
    hist_params = {'select': 'sport,team,season,stat_key,raw_value,rank,snapshot_date',
                   'snapshot_date': f'lt.{snap_date}'}
    if args.sport:
        hist_params['sport'] = f'eq.{args.sport}'
    hist = page(HIST, hist_params)
    latest: dict = {}
    for h in hist:
        k = _key(h)
        prev = latest.get(k)
        if prev is None or str(h['snapshot_date']) > str(prev['snapshot_date']):
            latest[k] = h
    print(f'  prior history rows: {len(hist)} ({len(latest)} distinct keys)')

    payload = []
    unchanged = 0
    for row in current:
        if not _changed(row, latest.get(_key(row))):
            unchanged += 1
            continue
        rec = {k: row.get(k) for k in ('sport', 'team', 'season', 'stat_key')}
        rec.update({f: row.get(f) for f in FIELDS})
        rec['snapshot_date'] = snap_date
        payload.append(rec)

    by_sport = defaultdict(int)
    for p in payload:
        by_sport[p['sport']] += 1
    print(f'  unchanged (skipped): {unchanged}')
    print(f'  to write: {len(payload)}  {dict(by_sport) or "{}"}')

    if not payload:
        print('\n  nothing changed since the last snapshot — no write needed.')
        return
    if args.dry_run:
        print('\n  DRY RUN — add no flag to write.')
        return

    written = 0
    for i in range(0, len(payload), 500):
        chunk = payload[i:i + 500]
        r = requests.post(
            f'{SB}/rest/v1/{HIST}'
            '?on_conflict=sport,team,season,stat_key,snapshot_date',
            headers=H_W, json=chunk, timeout=120)
        if r.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ write -> {r.status_code}: {(r.text or "")[:240]}')

    # Read back. A 2xx is not proof the rows landed — that lesson cost two
    # false "repaired 545/545" reports on 2026-09-25.
    chk = requests.get(f'{SB}/rest/v1/{HIST}',
                       headers={**H, 'Prefer': 'count=exact', 'Range': '0-0'},
                       params={'select': 'id', 'snapshot_date': f'eq.{snap_date}'},
                       timeout=60)
    landed = (chk.headers.get('content-range') or '').split('/')[-1]
    print(f'\n  wrote {written}/{len(payload)} · rows in table for {snap_date}: {landed}')
    if str(landed) == '0' and written:
        print('  🚨 WRITE REPORTED OK BUT NOTHING LANDED — verified by read-back.')
        sys.exit(2)


if __name__ == '__main__':
    main()
