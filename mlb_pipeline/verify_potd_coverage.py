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

A note on 'best_bet_<date>_<sport>' rows, because the first version of this
checker got them WRONG and counted them as ungraded gaps. They are deliberate
AUDIT ARCHIVES of a superseded pick, written by play_of_day_multi_sport.py just
before it overwrites the live key. A pick that was replaced was never published,
so grading it would push an overridden pick into the public record. They are
reported informationally and never counted as problems.

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

# 2026-10-22: this script prints box-drawing and check/cross glyphs, and a
# Windows console defaults to cp1252, so the FIRST finding it reports raises
# UnicodeEncodeError and the run dies before printing the rest. The workflow
# sets PYTHONIOENCODING=utf-8 so CI never saw it; running it by hand on
# Andy's box crashed on the very line that was about to say what was wrong.
# Same guard the other scripts in this folder already use.
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:                                          # noqa: BLE001
        pass

PLAIN_KEY = re.compile(r'^best_bet_(\d{4}-\d{2}-\d{2})$')
ANY_KEY = re.compile(r'^best_bet_(\d{4}-\d{2}-\d{2})(?:_(.+))?$')
DECIDED = {'Win', 'Loss', 'Push'}
# 'no-pick' is a legitimate outcome, not a gap: some days genuinely have no
# qualifying play once the juice and conviction gates are applied.
#: 'Void' belongs here too. A scratched starter or a DNP'd player settles the
#: bet - the book gives the stake back - and grade_potd has always treated it
#: as settled (`_prior in ('Win','Loss','Push','Void')`). This checker did
#: not, so it reported 2026-10-16 (Nick Martinez Over 3.5 Hits Allowed, Void)
#: as "still ungraded after the games finished" and would have gone on
#: reporting it forever: nothing can grade a bet that never ran. Void stays
#: out of DECIDED, so it is still excluded from the W-L record.
TERMINAL = DECIDED | {'no-pick', 'Void'}

# ══ 2026-10-22 · 'no-pick' IS ONLY TERMINAL WHEN THERE IS NO PICK ══
# Andy: "Why was POTD recoreded wrong, ensure that doesnt happen again."
#
# 10-21's POTD was 'Toronto Raptors -8.5' at 60 conviction. TOR won 135-92,
# so -8.5 covered by 34.5. It was stored as 'no-pick' - the app's record of
# a day we played and won said we never played.
#
# The parser hole that wrote it is fixed (grade_potd now matches a spread by
# SHAPE, not by looking for the words rl/ats/spread, which a basketball line
# does not carry). But THIS CHECKER WOULD NOT HAVE CAUGHT IT EITHER, and
# that is the part that matters for "doesn't happen again": 'no-pick' is in
# TERMINAL, so section 2 saw a settled row and said nothing. A wrong POTD
# grade was invisible to the one script whose whole job is POTD integrity.
#
# The rule that makes it visible is the rule grade_potd already states in
# its own guard: 'no-pick' is honest ONLY for a date with no pick. A row
# carrying a `lean` that names a play is a FALSE terminal - the pick existed
# and something failed to grade it.
#
# These lean values ARE genuine no-plays and must not be flagged. They are
# what the discipline gates write when nothing clears, and they are the
# reason 'no-pick' is in TERMINAL at all.
NO_PLAY_LEANS = ('no play', 'no games', 'no pick', 'skip', 'pass',
                 'no qualifying', 'none')


def _is_real_pick(lean) -> bool:
    """Does this lean name a PLAY, as opposed to recording a no-play?

    Substring, not equality: the field carries decorated text like
    'No Play (discipline pass)' and 'SKIP Away 1.5 (Jerry 87/100)'. Erring
    toward "not a pick" is the safe direction - a false alarm on a genuine
    no-play costs a line of log, while a missed one is a real result absent
    from the record.
    """
    s = str(lean or '').strip().lower()
    if not s:
        return False
    if any(k in s for k in NO_PLAY_LEANS):
        return False
    # A bare MATCHUP is not a pick either. play_of_day fell back to
    # "<away> @ <home>" whenever lean_display was missing, so 2026-10-10
    # reads 'Chicago White Sox @ Cleveland Guardians' - a string that names
    # no side and no market and that no parser can grade. Flagging it as a
    # recoverable no-pick would send someone to re-run grade_potd forever.
    # Shape test rather than the new leanIsMatchupFallback flag, because the
    # flag only exists on rows written from today on and this has to catch
    # the history too.
    if ' @ ' in s and not re.search(
            r'(?:\bml\b|\brl\b|\bats\b|spread|puck\s*line|run\s*line'
            r'|\bover\b|\bunder\b|[+-]\s*\d)', s):
        return False
    return True


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

    # ── 2b. FALSE 'no-pick': a settled row that names a real play ──
    # See the NO_PLAY_LEANS block at the top. This is the check that would
    # have caught 10-21's Raptors POTD, which was a Win stored as 'no-pick'.
    false_nopick = [(d, row) for d, row in sorted(h_by_date.items())
                    if str(row.get('result')) == 'no-pick'
                    and _is_real_pick(row.get('lean'))]
    if false_nopick:
        problems += len(false_nopick)
        print(f'\n  ✖ {len(false_nopick)} row(s) marked "no-pick" while the '
              f'lean names a PLAY. A pick we published is recorded as a day '
              f'we did not play, so a real result is missing from the '
              f'denominator:')
        for d, row in false_nopick[:15]:
            print(f'      {d}  sport={row.get("sport")}  '
                  f'lean={str(row.get("lean"))[:46]!r}')
        print('      -> re-run: python grade_potd.py --date <date>. If it '
              'still cannot read the pick, that is a PARSER defect; fix the '
              'parser rather than accepting the no-pick.')

    # ── 3. SUPERSEDED ARCHIVES — informational, NOT a gap ───────────────
    # CORRECTION 2026-09-29: the first version of this check counted these as
    # actionable and reported "7 ungraded". That was WRONG, and grading them
    # would have been actively harmful.
    #
    # best_bet_<date>_<sport> is written by play_of_day_multi_sport.py (line
    # ~251) as a deliberate AUDIT ARCHIVE of the OUTGOING pick, immediately
    # before it overwrites the live key — its docstring: "The MLB POTD write is
    # preserved in best_bet_{today}_mlb for audit/rollback." A superseded pick
    # was never published to users, so it must NOT be graded and must NOT reach
    # the Receipts calendar. grade_potd ignoring them is correct behaviour, not
    # a bug, and the lesson is the one from earlier the same day: verify what a
    # thing IS before reporting it broken.
    if suffixed:
        print(f'\n  ℹ {len(suffixed)} superseded archive row(s) '
              f'(best_bet_<date>_<sport>) — correctly ungraded, by design.')
        print('      These are outgoing picks preserved by '
              'play_of_day_multi_sport before an override. Never published, so '
              'never graded. NOT counted as a problem.')

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
