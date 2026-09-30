#!/usr/bin/env python3
"""Verify POTD exists, reached the Receipts calendar, and got graded — every sport.

Andy 2026-09-29: "queue up POTD verification for all sports and ensure Receipts
tab calendar is and record up to date for POTD".

WHY A VERIFIER AND NOT JUST A BACKFILL
--------------------------------------
POTD is the marquee daily pick and it lives across THREE surfaces that can
disagree, which is exactly how it drifted:

    jerry_cache          cache_key = 'best_bet_YYYY-MM-DD'  (+ '_<sport>' variants)
                         holds the pick and data.result
    daily_best_bet_history   bet_date + result — what the RECEIPTS CALENDAR reads
    the public record    derived from that history table

Measured 2026-09-29, before this existed:

    jerry_cache POTD dates ................... 155
    daily_best_bet_history rows ..............  140
    in jerry_cache but NOT on the calendar ...   33   (most of July)
    on the calendar with no jerry_cache POTD ..  18   (April — cache aged out)
    Pending on the calendar ...................   5   (4 stale + today)
    published record .......................... 74-55-4 (57.4%)

So the record was computed over 129 decided days while 33 POTDs never reached
the surface at all. Not wrong exactly — incomplete, silently, and no single
query would have told you.

TWO SEPARATE DEFECTS THIS CHECKS FOR
------------------------------------
1. COVERAGE — a POTD that exists in jerry_cache but has no calendar row. Those
   are invisible to users and absent from the record.
2. GRADING — a calendar row still 'Pending' after its games finished.
   grade_potd was wired 2026-09-08; it works going forward (28 of September's
   POTDs are graded) but never backfilled Apr-Aug, which is why 109 of the
   ungraded jerry_cache rows predate it.

Plus a KEY-FORMAT trap worth its own flag: 7 rows are
'best_bet_<date>_mlb' rather than 'best_bet_<date>'. grade_potd matches
`game_id = eq.best_bet_<date>` exactly, so every suffixed row is invisible to
it — graded=0 of 7. A second key format that silently bypasses the grader is
the same class as the five-copies-of-one-window bug: two spellings of one thing,
and only one of them is wired.

ALL SPORTS, deliberately. POTD is a universal pool but has been MLB on 137 of
140 calendar rows (NBA 2, NFL 1). Baseball ends in weeks, so the day POTD has to
come from NHL/NBA/NCAAF is close, and a verifier that only knows MLB would go
quiet at exactly the wrong moment rather than reporting the gap.

EXIT CODE is 1 when something actionable is found, so run_step.sh records it and
the nightly gate turns the run red. Read-only: fixes nothing, reports precisely.

USAGE
    python verify_potd_coverage.py              # last 45 days
    python verify_potd_coverage.py --days 200   # whole season
    python verify_potd_coverage.py --warn-only  # report, always exit 0
"""
import argparse
import collections
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV):
        _ln = _ln.strip()
        if _ln and not _ln.startswith('#') and '=' in _ln:
            _k, _v = _ln.split('=', 1)
            os.environ.setdefault(_k, _v.strip().strip('"'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not SB or not KEY:
    print('verify_potd_coverage: no Supabase credentials — skipping')
    sys.exit(0)
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

PLAIN_KEY = re.compile(r'^best_bet_(\d{4}-\d{2}-\d{2})$')
ANY_KEY = re.compile(r'^best_bet_(\d{4}-\d{2}-\d{2})(?:_(.+))?$')
DECIDED = {'Win', 'Loss', 'Push'}
# 'no-pick' is a legitimate outcome, not a gap: some days genuinely have no
# qualifying play once the juice and conviction gates are applied.
TERMINAL = DECIDED | {'no-pick'}


def _page(tbl, select, extra=None):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off)}
        if extra:
            p.update(extra)
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, params=p, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f'{tbl} -> {r.status_code}: {(r.text or "")[:200]}')
        body = r.json()
        if not isinstance(body, list) or not body:
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _result_of(row):
    d = row.get('data')
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            d = {}
    return (d or {}).get('result') if isinstance(d, dict) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=45,
                    help='How far back to check (default 45)')
    ap.add_argument('--warn-only', action='store_true')
    a = ap.parse_args()

    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    since = (today - timedelta(days=a.days)).isoformat()

    cache = _page('jerry_cache', 'cache_key,sport,data',
                  {'cache_key': 'like.best_bet_*'})
    hist = _page('daily_best_bet_history',
                 'bet_date,sport,result,lean,resolved_at')

    # Index by date, keeping suffixed variants separate — they are the trap.
    by_date, suffixed = {}, []
    for row in cache:
        m = ANY_KEY.match(row.get('cache_key') or '')
        if not m:
            continue
        d = m.group(1)
        if d < since:
            continue
        if m.group(2):
            suffixed.append((d, row))
        else:
            by_date[d] = row
    h_by_date = {str(x['bet_date'])[:10]: x for x in hist
                 if str(x.get('bet_date') or '')[:10] >= since}

    print(f'=== POTD coverage · {since} .. {today} ({a.days}d) ===')
    print(f'  jerry_cache POTD dates    : {len(by_date)}')
    print(f'  calendar rows             : {len(h_by_date)}')

    problems = 0

    # ── 1. COVERAGE: a POTD with no calendar row is invisible to users ──
    missing = sorted(set(by_date) - set(h_by_date))
    if missing:
        problems += len(missing)
        print(f'\n  ✖ {len(missing)} POTD(s) NOT on the Receipts calendar '
              f'(absent from the record):')
        for d in missing[:15]:
            print(f'      {d}  sport={by_date[d].get("sport")}')
        if len(missing) > 15:
            print(f'      … and {len(missing) - 15} more')

    # ── 2. GRADING: a past calendar row still Pending ──
    stale = []
    for d, row in sorted(h_by_date.items()):
        if d >= today.isoformat():
            continue          # today's games may still be running
        if str(row.get('result')) not in TERMINAL:
            stale.append((d, row))
    if stale:
        problems += len(stale)
        print(f'\n  ✖ {len(stale)} calendar row(s) still ungraded after the games '
              f'finished:')
        for d, row in stale[:15]:
            print(f'      {d}  sport={row.get("sport")}  '
                  f'result={row.get("result")}  {str(row.get("lean"))[:34]}')

    # ── 3. KEY FORMAT: suffixed rows grade_potd cannot see ──
    if suffixed:
        ungraded_sfx = [(d, r) for d, r in suffixed if not _result_of(r)]
        print(f'\n  ⚠ {len(suffixed)} suffixed best_bet_<date>_<sport> row(s); '
              f'{len(ungraded_sfx)} ungraded')
        print('      grade_potd matches game_id = best_bet_<date> EXACTLY, so '
              'these bypass it entirely.')
        for d, r in ungraded_sfx[:8]:
            print(f'      {r.get("cache_key")}  sport={r.get("sport")}')
        if ungraded_sfx:
            problems += len(ungraded_sfx)

    # ── 4. DRIFT: jerry_cache and the calendar disagreeing on an outcome ──
    drift = []
    for d, row in sorted(h_by_date.items()):
        if d not in by_date:
            continue
        jr, hr = _result_of(by_date[d]), row.get('result')
        if jr and hr and str(jr) != str(hr):
            drift.append((d, jr, hr))
    if drift:
        problems += len(drift)
        print(f'\n  ✖ {len(drift)} date(s) where jerry_cache and the calendar '
              f'disagree on the result:')
        for d, jr, hr in drift[:10]:
            print(f'      {d}  jerry_cache={jr}  calendar={hr}')

    # ── 5. THE PUBLISHED RECORD, and its denominator ──
    w = sum(1 for x in hist if x.get('result') == 'Win')
    l = sum(1 for x in hist if x.get('result') == 'Loss')
    p = sum(1 for x in hist if x.get('result') == 'Push')
    pend = sum(1 for x in hist if x.get('result') not in TERMINAL)
    print(f'\n  POTD record (all time, from the calendar): {w}-{l}-{p}'
          + (f'  ({100 * w / (w + l):.1f}%)' if (w + l) else ''))
    print(f'    decided {w + l + p} · still pending {pend} · calendar rows {len(hist)}')
    bysport = collections.Counter(str(x.get('sport')) for x in hist)
    print(f'    by sport: {dict(bysport)}')
    if len(bysport) and bysport.most_common(1)[0][1] > 0.9 * len(hist):
        top = bysport.most_common(1)[0][0]
        print(f'    ⚠ {top} is >90% of all POTD history. POTD is a universal '
              f'pool, so when {top} goes offline the other sports must be able '
              f'to supply it — otherwise the surface goes blank, not quiet.')

    if problems and not a.warn_only:
        print(f'\n{problems} actionable POTD issue(s). '
              f'Fix coverage with grade_potd.py --backfill N.')
        return 1
    if not problems:
        print('\nPOTD coverage, grading and record all consistent.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
