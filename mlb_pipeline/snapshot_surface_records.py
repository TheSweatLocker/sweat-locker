#!/usr/bin/env python3
"""Snapshot surface_records once per ET day, so a record change is detectable.

══ WHY (B172) ══════════════════════════════════════════════════════════
On 2026-10-23 the all-time Sharp went +81.21u -> +36.77u overnight. The W-L
barely moved; only the pricing basis did. Nothing alerted, nothing was logged,
and the only detector in the system was Andy reading the card the next morning
and asking why the units were half what they had been.

It was undetectable after the fact because surface_records holds exactly ONE
row per (surface, sport, window_key), updated in place. There is no history,
so the previous value was simply gone -- the size of the move could only be
recovered from a code comment that happened to record it.

This writes the missing memory: one row per key per ET day. With two days on
disk, check_record_discontinuity() in watchdogs.py can ask the question that
matters -- did units move by MORE than the newly-graded picks could explain?

ET, not UTC. A UTC day boundary is 8pm ET, so a UTC-stamped snapshot would
split one slate across two dates
(feedback_window_from_runner_clock_is_a_day_ahead).

Idempotent: the unique index is (snapshot_date, surface, sport, window_key)
and this upserts with merge-duplicates, so running it four times a day leaves
one row per key per day holding the latest values. Deliberately NOT
ignore-duplicates -- that would freeze the first run of the day and miss
exactly the intraday change this exists to catch (the 10-23 move happened
between two same-day runs).

Usage:
    python snapshot_surface_records.py            # dry run
    python snapshot_surface_records.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        if '=' in _ln and not _ln.strip().startswith('#'):
            _k, _v = _ln.strip().split('=', 1)
            os.environ.setdefault(_k, _v)

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = dict(H, **{'Content-Type': 'application/json',
                'Prefer': 'resolution=merge-duplicates,return=minimal'})

FIELDS = ('surface', 'sport', 'window_key', 'wins', 'losses', 'pushes',
          'picks_count', 'hit_rate', 'units_net', 'roi_pct', 'epoch_start',
          'last_pick_date')


def et_today() -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(hours=4)).date().isoformat()


def page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, timeout=60,
                         params={**params, 'offset': str(off),
                                 'limit': '1000'})
        if r.status_code not in (200, 206):
            print(f'  ⚠ GET {path} HTTP {r.status_code}: {r.text[:200]}')
            return out
        b = r.json() or []
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    day = et_today()
    live = page('surface_records',
                {'select': ','.join(FIELDS) + ',last_computed_at',
                 'order': 'surface.asc'})
    print(f'=== snapshot_surface_records · ET {day} · '
          f'{"APPLY" if a.apply else "DRY RUN"} ===')
    if not live:
        # 0 rows read is a wiring error, not an empty day
        # (feedback_zero_rows_is_a_wiring_error).
        print('  ⚠ surface_records returned 0 rows — NOT writing an empty '
              'snapshot. Fix the read first.')
        return 1
    rows = []
    for r in live:
        rows.append({'snapshot_date': day,
                     **{k: r.get(k) for k in FIELDS},
                     'source_computed_at': r.get('last_computed_at')})
    print(f'  live surface_records rows: {len(rows)}')
    if not a.apply:
        print('  dry run — nothing written')
        return 0
    wrote = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        up = requests.post(
            f'{SB}/rest/v1/surface_records_history'
            f'?on_conflict=snapshot_date,surface,sport,window_key',
            headers=HW, timeout=120, data=json.dumps(chunk))
        if up.status_code not in (200, 201, 204):
            print(f'  ⚠ POST HTTP {up.status_code}: {up.text[:220]}')
            continue
        wrote += len(chunk)
    # A 204 is not a write (feedback_204_is_not_a_write): count what landed.
    back = page('surface_records_history',
                {'select': 'id', 'snapshot_date': f'eq.{day}'})
    print(f'  claimed {wrote}; stored for {day}: {len(back)}')
    if len(back) < len(rows):
        print('  ⚠ FEWER rows stored than sent — snapshot is incomplete, so '
              'treat tomorrow\'s discontinuity check as unreliable.')
        return 1
    days = page('surface_records_history',
                {'select': 'snapshot_date', 'order': 'snapshot_date.desc',
                 'limit': '1000'})
    uniq = sorted({str(x.get('snapshot_date')) for x in days}, reverse=True)
    print(f'  snapshot days on disk: {len(uniq)}  (latest: {uniq[:3]})')
    if len(uniq) < 2:
        print('  note: the discontinuity check needs TWO days — it will stay '
              'quiet until tomorrow\'s snapshot lands.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
