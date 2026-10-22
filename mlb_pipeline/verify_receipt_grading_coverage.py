#!/usr/bin/env python3
"""verify_receipt_grading_coverage.py - did everything we published get graded?

== WHY ==
2026-10-22. Andy, on accepting ~250 September MLB prop receipts as
permanently ungraded: *"Fine taking those 250 mlb prop as not graded. Seems
liek the minority as long as that many plays arent going ungrqdaed going
foraard across any sport."*

That is a condition, so it needs a check rather than an intention. Measured
the day he said it:

    month      graded   ungraded   ungraded %
    2026-08      5075         10       0.2%
    2026-09     11224        226       2.0%     <- the backlog he accepted
    2026-10      3165          8       0.3%

and over the last 14 days, 2,012 graded against 6 ungraded, with MLB, NBA,
NFL, NHL and UFC all at ZERO. So September was an anomaly, not a trend. This
script exists to make that falsifiable every day instead of quarterly when
someone notices.

== WHAT IT CHECKS ==
Ungraded receipts on dates that have FINISHED, inside a recent window, per
sport. Three deliberate choices:

  * `game_date < today ET`, so tonight's unplayed slate is never counted as
    a failure. Using the runner's date here would mark a whole evening's
    picks as ungraded for the four hours UTC and ET disagree
    (feedback_window_from_runner_clock_is_a_day_ahead).
  * A WINDOW, not all time. Andy accepted the September backlog; re-reporting
    it daily would bury the one new row that matters. The window is what
    "going forward" means.
  * PER SPORT. A single global percentage hides a sport going dark - 20
    ungraded NHL rows vanish inside 2,000 graded NFL ones.

== WHAT IT DOES NOT DO ==
It does not grade anything. It measures, and it names each row with its
source_table so the finding is actionable - the source_table is what says
WHICH grader is failing, and that is the whole diagnostic value.

Thresholds are deliberately loose. The point is to catch a surface going
dark, not to fail the build over one stuck receipt.

Usage:
    python verify_receipt_grading_coverage.py                 # 14d
    python verify_receipt_grading_coverage.py --days 30
    python verify_receipt_grading_coverage.py --warn-only     # always exit 0
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
import sys
from collections import defaultdict, Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:                                          # noqa: BLE001
        pass

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: Above this share of a sport's receipts ungraded, inside the window, say so
#: loudly. 5% against a measured October baseline of 0.3% is ~15x the normal
#: rate - far enough out to mean something changed, close enough that one
#: stuck row on a quiet NHL night does not trip it.
PCT_WARN = 5.0
#: An absolute floor so a thin sport cannot trip the percentage. 2 ungraded of
#: 10 is 20% and is not news; 25 ungraded is, at any denominator.
MIN_N_FOR_PCT = 40
COUNT_WARN = 25

#: Receipts that are ungraded for a reason already investigated and accepted.
#: Kept as a NAMED, DATED list rather than a silent filter, because an
#: exemption that nobody can see becomes the next invisible backlog.
KNOWN_UNGRADEABLE = (
    # B125: the score sits on the home/away-inverted twin row, so the
    # results row this receipt points at has NULL scores. Grading it would
    # mean choosing which feed is right about who was home.
    ('NCAAF', '2026-10-10'),
)


def _et_today() -> dt.date:
    now_utc = dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo('America/New_York')).date()
    except Exception:                                          # noqa: BLE001
        return (now_utc - dt.timedelta(hours=5)).date()


def page(tbl, params):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H,
                         params={**params, 'limit': '1000',
                                 'offset': str(off)}, timeout=90)
        if r.status_code != 200:
            raise RuntimeError(f'{tbl} HTTP {r.status_code}: {r.text[:200]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _norm(x):
    return {'w': 'WIN', 'win': 'WIN', 'l': 'LOSS', 'loss': 'LOSS',
            'p': 'PUSH', 'push': 'PUSH', 'void': 'VOID'}.get(
        str(x or '').strip().lower())


def check_fade_consistency(days: int) -> int:
    """A FADE receipt must SHOW, and be GRADED ON, the side we backed.

    == WHY THIS IS STRUCTURAL, NOT A BOX-SCORE CHECK ==
    A FADE read backs the OPPOSITE side of the prop it is reading. Two things
    must therefore be true of its receipt, and neither needs a game result:

      1. pick_side must be the opposite of the prop's `direction`. A FADE
         receipt whose pick_side EQUALS direction is displaying the side we
         faded as though we backed it. Wrong by construction.
      2. its result must match the read's, because prop_jerry_reads.result
         grades the side we backed - verified from the box score (Dustin May
         ha_under 3.5, final 3 hits: the under hit, so our OVER lost, and the
         read says LOSS).

    Both halves were broken, in opposite directions. The LABEL was fixed
    mid-September (July+August: 0 of 559 right; October: 24 of 24). The GRADE
    was not, which left receipts reading "Dustin May OVER 3.5 - WIN" when the
    over lost: a correct label with a contradictory grade, worse than either
    error alone because it looks right.

    Andy on the history: "If it s props from a whiel ago leave record as is
    and just make sure we wont have this problem going forward." So this
    checks a WINDOW - the July-September rows stay as they are and are not
    re-reported daily. What this catches is a regression.
    """
    today = _et_today()
    start = (today - dt.timedelta(days=days)).isoformat()
    cut = today.isoformat()
    try:
        reads = {str(x['id']): x for x in page(
            'prop_jerry_reads', {'select': 'id,call_verdict,direction,result'})}
        recs = page('public_receipts',
                    {'select': 'id,source_id,source_table,pick_side,'
                               'pick_label,result,game_date',
                     'and': f'(game_date.gte.{start},game_date.lt.{cut})'})
    except Exception as e:                                     # noqa: BLE001
        print(f'\n  fade check UNAVAILABLE ({type(e).__name__}: {e}) - '
              f'treat as unverified, not as a pass')
        return 0
    label_bad, grade_bad, n = [], [], 0
    for r in recs:
        if r.get('source_table') != 'prop_jerry_reads':
            continue
        s_ = reads.get(str(r.get('source_id')))
        if not s_ or str(s_.get('call_verdict') or '').upper() != 'FADE':
            continue
        n += 1
        d = str(s_.get('direction') or '').upper()
        ps = str(r.get('pick_side') or '').upper()
        if d and ps and d == ps:
            label_bad.append(r)
        a_, b_ = _norm(r.get('result')), _norm(s_.get('result'))
        if a_ and b_ and a_ != b_:
            grade_bad.append((r, a_, b_))
    print(f'\n  FADE receipts in window: {n}')
    if not n:
        print('    none in window - nothing to check')
        return 0
    if label_bad:
        print(f'    ** {len(label_bad)} show the side we FADED as our pick '
              f'(pick_side == prop direction - wrong by construction):')
        for r in label_bad[:8]:
            print(f'       {r["game_date"]} {str(r.get("pick_label"))[:40]}')
    if grade_bad:
        print(f'    ** {len(grade_bad)} graded on the wrong side (receipt '
              f'disagrees with its read, which grades OUR side):')
        for r, a_, b_ in grade_bad[:8]:
            print(f'       {r["game_date"]} '
                  f'{str(r.get("pick_label"))[:34]:34s} '
                  f'receipt={a_} read={b_}')
    if not (label_bad or grade_bad):
        print('    PASS - every FADE receipt shows and grades the backed side')
    return len(label_bad) + len(grade_bad)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--warn-only', action='store_true',
                    help='report and always exit 0')
    a = ap.parse_args()

    today = _et_today()
    start = (today - dt.timedelta(days=a.days)).isoformat()
    cut = today.isoformat()

    rows = page('public_receipts',
                {'select': 'id,sport,surface,source_table,game_date,result,'
                           'pick_label',
                 'and': f'(game_date.gte.{start},game_date.lt.{cut})'})
    print(f'=== receipt grading coverage · {start} .. {cut} (ET) ===')
    print(f'  {len(rows)} receipt(s) on finished dates')
    if not rows:
        print('  0 receipts in the window - WIRING ERROR, not a clean bill')
        return 0 if a.warn_only else 1

    per = defaultdict(lambda: [0, 0])
    ungraded = []
    for r in rows:
        k = str(r.get('sport') or '?')
        if r.get('result') is None:
            per[k][1] += 1
            ungraded.append(r)
        else:
            per[k][0] += 1

    print(f"\n  {'sport':8s} {'graded':>7s} {'ungraded':>9s} {'%':>7s}")
    problems = []
    for k in sorted(per):
        g, u = per[k]
        tot = g + u
        pct = 100 * u / tot if tot else 0.0
        flag = ''
        if u >= COUNT_WARN:
            flag = f'  ** {u} ungraded'
            problems.append((k, u, pct, 'count'))
        elif tot >= MIN_N_FOR_PCT and pct > PCT_WARN:
            flag = f'  ** {pct:.1f}% > {PCT_WARN}%'
            problems.append((k, u, pct, 'pct'))
        print(f'  {k:8s} {g:7d} {u:9d} {pct:6.1f}%{flag}')

    if ungraded:
        # source_table is the diagnostic: it names WHICH grader is failing.
        print(f'\n  the {len(ungraded)} ungraded, by source_table:')
        for st, n in Counter(str(r.get('source_table'))
                             for r in ungraded).most_common():
            print(f'     {st:26s} {n}')
        known = {(s, d) for s, d in KNOWN_UNGRADEABLE}
        shown = 0
        for r in sorted(ungraded, key=lambda x: str(x.get('game_date'))):
            tag = ''
            if (str(r.get('sport')), str(r.get('game_date'))[:10]) in known:
                tag = '  [known-ungradeable]'
            if shown < 20:
                shown += 1
                print(f"     {r['game_date']} {str(r.get('sport')):6s} "
                      f"{str(r.get('surface')):11s} "
                      f"{str(r.get('pick_label'))[:30]:30s}"
                      f"{tag}")
        if len(ungraded) > 20:
            print(f'     ... and {len(ungraded) - 20} more')

    _fade = check_fade_consistency(a.days)
    if _fade:
        problems.append(('FADE-SIDE', _fade, 0.0, 'fade side/grade'))

    if problems:
        print(f'\n  {len(problems)} sport(s) above threshold:')
        for k, u, pct, why in problems:
            print(f'     {k}: {u} ungraded ({pct:.1f}%) - tripped on {why}')
        print('  A surface going dark looks exactly like this. Check the '
              'source_table above: it names the grader that stopped.')
    else:
        print(f'\n  PASS - no sport above {PCT_WARN}% or {COUNT_WARN} '
              f'ungraded in {a.days}d.')
        print(f'  Baseline for reference: October measured 0.3% ungraded, '
              f'September 2.0% (the accepted backlog).')

    return 0 if (a.warn_only or not problems) else 1


if __name__ == '__main__':
    sys.exit(main())
