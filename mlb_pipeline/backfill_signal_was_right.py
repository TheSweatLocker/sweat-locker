#!/usr/bin/env python3
"""Populate signal_attribution.signal_was_right on already-graded rows.

== WHY (2026-10-29) ========================================================
`signal_attribution.result` is the PICK's outcome, stamped onto every signal
row that fired on that pick. `v_signal_records` -- which the game card
tooltip shows a user -- counted it, so every signal displayed OUR OWN
selection record rather than its own. Measured on signals with n>=25 both
ways, 11 of 34 were inflated:

    NCAAF COHORT_NCAAF_HEAVY_HOME_DOG    43.8% shown    60.0% real  +16.2pp
    NCAAF home_team_ats_hot_season       64.3%          50.0%       -14.3pp
    NFL   COHORT_NFL_DIV_HOME_COVER      46.9%          33.3%       -13.5pp
    NCAAF COHORT_NCAAF_HEAVY_HOME_FAV    61.7%          50.9%       -10.8pp
    NFL   LR_SHADOW                      53.3%          42.7%       -10.7pp

The error is not random: it penalises a signal for DISAGREEING with the
engine, which is exactly the dissenting voice we most want measured
honestly. Same family as project_signal_hit_rate_was_our_pick_rate_1028.

20261029d adds the column and repoints the view at it. The view counts only
non-NULL rows, so until this backfill runs the tooltip shows NOTHING rather
than something wrong -- which is the correct failure direction, but it does
mean this should run right after the migration.

`signal_outcome.signal_was_right` is the single definition of the rule and
returns None for a row it cannot settle (NEUTRAL subject, cross-axis, push).
None is written as NULL and excluded by the view -- skipped, not defaulted.
"""
from __future__ import annotations
import argparse, os, sys
from collections import Counter
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')
SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json'}

from signal_outcome import signal_was_right as swr


def page(params: dict) -> list:
    """Paged read. PostgREST silently caps an unpaged read at 1000, and this
    table has thousands of rows (feedback_never_measure_from_truncated_output)."""
    out, off = [], 0
    while True:
        p = dict(params, limit=1000, offset=off)
        r = requests.get(f'{SB}/rest/v1/signal_attribution',
                         headers=H_READ, params=p, timeout=90)
        if r.status_code != 200:
            print(f'  ! read failed {r.status_code}: {r.text[:200]}')
            return out
        batch = r.json() or []
        out += batch
        if len(batch) < 1000:
            return out
        off += 1000
        if off > 500_000:
            print('  ! paging exceeded 500k rows - stopping')
            return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='write; default is a dry run')
    ap.add_argument('--only-null', action='store_true',
                    help='skip rows that already have a value')
    args = ap.parse_args()

    params = {'select': '*', 'result': 'in.(W,L,P)', 'order': 'id.asc'}
    if args.only_null:
        params['signal_was_right'] = 'is.null'
    rows = page(params)
    print(f'=== backfill_signal_was_right · {len(rows)} graded rows'
          f'{" [DRY RUN]" if not args.apply else ""} ===')
    if not rows:
        print('  nothing to do')
        return 0
    if 'signal_was_right' not in rows[0]:
        print('  ! signal_attribution has no signal_was_right column - '
              'apply 20261029d first. Nothing written.')
        return 2

    verdicts: Counter = Counter()
    changed, failed = [], 0
    for row in rows:
        try:
            v = swr(row, row.get('sport'))
        except Exception as e:                                 # noqa: BLE001
            failed += 1
            if failed <= 3:
                print(f'  ! id={row.get("id")}: {e}')
            continue
        verdicts[str(v)] += 1
        if row.get('signal_was_right') != v:
            changed.append((row['id'], v))

    print(f'  verdicts: {dict(verdicts)}   (None = not countable, '
          f'written as NULL and excluded by the view)')
    print(f'  rows whose stored value would change: {len(changed)}')
    if failed:
        print(f'  ! {failed} row(s) raised and were left untouched')

    # Compare what the OLD view counted against what the new one will, so the
    # effect is measured rather than assumed.
    old_w = sum(1 for r in rows if r.get('result') == 'W')
    old_n = sum(1 for r in rows if r.get('result') in ('W', 'L'))
    new_w = verdicts['True']
    new_n = verdicts['True'] + verdicts['False']
    if old_n and new_n:
        print(f'  pooled: shown {100*old_w/old_n:.1f}% (n={old_n}) -> '
              f'real {100*new_w/new_n:.1f}% (n={new_n})')

    if not args.apply:
        print('  DRY RUN - wrote nothing')
        return 0

    ok = 0
    for rid, v in changed:
        r = requests.patch(f'{SB}/rest/v1/signal_attribution?id=eq.{rid}',
                           headers=H_WRITE, json={'signal_was_right': v},
                           timeout=15)
        if r.status_code in (200, 204):
            ok += 1
        elif ok == 0:
            print(f'  ! patch id={rid} failed {r.status_code}: {r.text[:160]}')
            break
    print(f'  wrote {ok} of {len(changed)}')
    return 0 if ok == len(changed) else 1


if __name__ == '__main__':
    sys.exit(main())
