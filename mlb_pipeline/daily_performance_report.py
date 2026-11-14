#!/usr/bin/env python3
"""daily_performance_report.py — last night, every surface, every sport.

══ WHY THIS EXISTS ══════════════════════════════════════════════════════
Andy, 2026-11-14: "what do we need to do to have a better product tomorrow
and better prepared for morning cadence, we should be assessing day to day
performance across every surface in every sport".

Nothing did this. Three things come close and each misses a different half:

  * morning_audit.py prints a 30-DAY board. A 30-day window structurally
    cannot tell you last night went badly: one bad night moves a 129-pick
    denominator by under a point.
  * compute_surface_records.py knows all 25 surfaces x 8 sports and is the
    single source of truth for what counts as a pick on each — but its
    windows are mtd / prev_month / d7 / d30 / epoch / lifetime. There is no
    daily one, and adding one THERE would publish a one-night record to the
    Receipts tab, where an 0-3 night reads as a 0% surface.
  * aggregate_daily_records.py does write per-(surface, sport, date) rows,
    but only 6-10 a night across 12 surfaces, and the set it covers is
    missing sweat_card (the dashboard itself), game_read (our highest-volume
    surface, 538-386 over 30 days) and every <sport>_sides surface.

So this reports over the SAME pickers compute_surface_records defines, on a
one-day window, each cell beside its own d7/d30 baseline. It deliberately
imports them rather than restating what a sweat_card pick is: a second
definition of a surface is how two codepaths over one feed start to
disagree ([[feedback_a_blocklist_duplicating_a_lookup_table]]).

══ THE COMPLETENESS GATE RUNS FIRST, AND IT IS NOT DECORATION ═══════════
Every picker in compute_surface_records filters `result=not.is.null`. That
is right for a record — you cannot sum an ungraded pick — but it means an
ungraded pick is INVISIBLE to every record surface we own. A night that
graded 14 of its 60 picks reports the 14 and says nothing about the 46, so
a BROKEN night looks like a THIN night. Measured on 2026-11-13 at 04:11 ET:
72 of 1,583 receipts ungraded, including game_read NCAAB 46 of 60 and every
single NBA receipt on the slate.

The cause was upstream of grading both times, which is why the gate reports
finals before it reports picks:

    NBA   2026-11-13   0 of  9 games carried a score
    NCAAB 2026-11-13   6 of 52 games carried a score

══ "NOT YET" IS NOT "BROKEN" ════════════════════════════════════════════
Run at 04:11 ET, every surface that builds at ~08:00 ET reads MISSING and
every resolver that fires at 06:23 ET reads dark. A check that is red every
night at 4am is a check nobody reads
([[feedback_a_detector_must_know_the_intentional_case]]). So each sport
carries the UTC time of its own earliest daily resolver, and an unscored
final is a WARNING until that time plus a grace, and a FAILURE after.

The grace is deliberately wide. nba_pipeline.yml's own comment measures its
09:00 ET cron landing at "~1:24pm ET at median lateness" — GitHub cron
lateness of four hours is normal here, so a tight grace would just relocate
the false alarm.

══ NO RATE UNDER n=30 ═══════════════════════════════════════════════════
House rule, and it binds hardest on a daily report: a single day almost
never reaches n=30 in one cell. The DAY column therefore prints W-L-P and
units and NO percentage; the rate appears only in d7/d30 and only where
that window itself carries n>=30 ([[feedback_sample_size_with_pct]]).

Units on some surfaces are a mixed price basis — see the warning in
compute_surface_records.pick_sweat_card. Wins/losses are exact everywhere.

CLI
  python daily_performance_report.py                 # yesterday ET
  python daily_performance_report.py --date 2026-11-13
  python daily_performance_report.py --no-trend      # gate + day only, fast
  python daily_performance_report.py --surface game_read --surface potd
  python daily_performance_report.py --json

EXIT  0 = day complete   1 = provisional (picks still awaiting a grade)
      2 = finals missing past their resolver window, or a query broke
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

import compute_surface_records as C  # noqa: E402  — the pickers ARE the contract

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

SB = C.SB
H = C.H

#: Per-sport final-score table. All six team sports share
#: (game_date, home_score, away_score) — verified against the live schema,
#: not assumed ([[feedback_select_star_before_concluding_absent]]). UFC is
#: keyed (event_date, winner) and so is handled separately below.
RESULTS_TABLE = {
    'MLB':   'mlb_game_results',
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NBA':   'nba_game_results',
    'NHL':   'nhl_game_results',
    'NCAAB': 'ncaab_game_results',
}

#: The EARLIEST daily cron in each sport's pipeline that resolves last
#: night's finals, as UTC HH:MM read straight off .github/workflows.
#: Deliberately UTC: every one of those files comments its crons in EDT, so
#: in November each is an hour earlier in ET than its own comment claims,
#: and converting through the comment would bake that error in.
#:
#:   NCAAB  '23 11 * * *'  ncaab_pipeline.yml  (comment says 7am ET)
#:   NHL    '5 12 * * *'   nhl_pipeline.yml
#:   NBA    '13 12 * * *'  nba_pipeline.yml
#:   MLB    '0 6 * * *'    mlb_pipeline.yml
#:   NCAAF  '27 6 * * 0'   ncaaf_pipeline.yml  (Sunday only — weekly sport)
#:   NFL    '10 12 * * 3'  nfl_pipeline.yml    (weekday-gated — weekly sport)
#:
#: NFL and NCAAF are weekly: their resolvers do not run every day, so a
#: Tuesday with unscored Sunday finals is real and a Monday morning one is
#: not yet late. Both are given the late slot rather than a daily one.
RESOLVER_UTC = {
    'NCAAB': '11:23',
    'NHL':   '12:05',
    'NBA':   '12:13',
    'MLB':   '06:00',
    'NCAAF': '13:27',
    'NFL':   '12:10',
}

#: Minutes after a resolver's scheduled time before an unscored final counts
#: as a failure rather than a wait. See the module docstring: four hours of
#: GitHub cron lateness is documented in nba_pipeline.yml itself.
DEFAULT_GRACE_MIN = 300

#: public_receipts is the freeze table for every PUBLISHED surface — these
#: nine are what it actually holds (measured, not assumed). The <sport>_sides
#: and prop-tier surfaces in compute_surface_records are DERIVED records
#: computed off results tables, not published picks, so they have no
#: ungraded state to report and are correctly absent here.
RECEIPT_SURFACES = ('prop_jerry', 'game_read', 'sweat_card', 'ledger',
                    'fade', 'sharp_card', 'potd', 'dawg', 'daily_degen')


def _et_today() -> dt.date:
    """Today in ET. Delegates to the one DST-correct helper in the repo."""
    return C._et_today()


def _paged(table: str, select: str, **filters):
    """Every row matching `filters`, paged. Raises on a non-200.

    It raises on purpose. A swallowed error here returns an empty list, and
    an empty list is indistinguishable from "nothing was ungraded" — which
    is the exact shape of a report that says a broken night was clean
    ([[feedback_zero_rows_is_a_wiring_error]]).
    """
    out, off = [], 0
    while True:
        params = {'select': select, 'limit': 1000, 'offset': off, **filters}
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                         params=params, timeout=90)
        if r.status_code != 200:
            raise RuntimeError(f'{table}: {r.status_code} {r.text[:200]}')
        body = r.json() or []
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _resolver_due(sport: str, now_utc: dt.datetime, day: dt.date,
                  grace_min: int) -> tuple[bool, str]:
    """(is the resolver overdue for `day`, human note).

    The resolver for `day`'s games runs the MORNING AFTER, so the deadline
    is day+1 at the sport's own cron time, plus the grace.
    """
    hhmm = RESOLVER_UTC.get(sport)
    if not hhmm:
        return False, 'no daily resolver on record'
    hh, mm = (int(x) for x in hhmm.split(':'))
    due = dt.datetime.combine(
        day + dt.timedelta(days=1), dt.time(hh, mm),
        tzinfo=dt.timezone.utc) + dt.timedelta(minutes=grace_min)
    if now_utc < due:
        mins = int((due - now_utc).total_seconds() // 60)
        return False, f'resolver window open for another {mins // 60}h{mins % 60:02d}m'
    return True, f'resolver was due {hhmm}Z +{grace_min}m'


def finals_census(day: dt.date, now_utc: dt.datetime, grace_min: int) -> dict:
    """Per sport: games on `day`, how many carry a final score.

    This runs BEFORE the pick census because it is the usual cause. On
    2026-11-13 the grader was working perfectly and had simply been handed
    6 of 52 NCAAB finals ([[feedback_green_step_that_writes_nothing]]).
    """
    out = {}
    for sport, table in RESULTS_TABLE.items():
        try:
            rows = _paged(table, 'game_date,home_score,away_score',
                          game_date=f'eq.{day.isoformat()}')
        except RuntimeError as e:
            out[sport] = {'error': str(e)}
            continue
        scored = sum(1 for r in rows
                     if r.get('home_score') is not None
                     and r.get('away_score') is not None)
        overdue, note = _resolver_due(sport, now_utc, day, grace_min)
        out[sport] = {
            'games': len(rows), 'scored': scored,
            'unscored': len(rows) - scored,
            'overdue': overdue and (len(rows) - scored) > 0,
            'note': note,
        }
    try:
        rows = _paged('ufc_fight_results', 'event_date,winner',
                      event_date=f'eq.{day.isoformat()}')
        done = sum(1 for r in rows if r.get('winner'))
        out['UFC'] = {'games': len(rows), 'scored': done,
                      'unscored': len(rows) - done, 'overdue': False,
                      'note': 'event-driven, no daily resolver'}
    except RuntimeError as e:
        out['UFC'] = {'error': str(e)}
    return out


def pick_census(day: dt.date, finals: dict) -> dict:
    """Per (surface, sport) on `day`: picks, graded, ungraded.

    A pick is only OVERDUE if its sport has every final in. Props written
    days before kickoff are legitimately ungraded — nfl_pipeline_props runs
    455 of 495 ungraded on a Thursday, all on games nobody has played — so
    counting them would bury the real signal under the schedule.
    """
    rows = _paged('public_receipts', 'surface,sport,result,pick_label',
                  game_date=f'eq.{day.isoformat()}')
    cells: dict = defaultdict(lambda: {'picks': 0, 'graded': 0, 'ungraded': 0})
    for r in rows:
        surface = r.get('surface') or '?'
        sport = str(r.get('sport') or '?').upper()
        c = cells[(surface, sport)]
        c['picks'] += 1
        if r.get('result') in (None, ''):
            c['ungraded'] += 1
        else:
            c['graded'] += 1
    for (surface, sport), c in cells.items():
        f = finals.get(sport) or {}
        waiting = int(f.get('unscored') or 0)
        c['blocked_on_finals'] = waiting
        # Overdue only once the scores it needs are actually in.
        c['overdue'] = c['ungraded'] > 0 and waiting == 0
    return dict(cells)


def _fmt_cell(agg: dict | None, with_rate: bool) -> str:
    """W-L-P + units, and a rate ONLY where the window earns one."""
    if not agg:
        return '—'
    w, l, p = agg['wins'], agg['losses'], agg['pushes']
    body = f"{w}-{l}" + (f"-{p}" if p else "")
    units = agg['units_net']
    out = f"{body:>9s} {units:+6.1f}u"
    if with_rate:
        n = w + l
        if n >= 30 and agg.get('hit_rate') is not None:
            out += f"  {agg['hit_rate'] * 100:5.1f}% (n={n})"
        else:
            out += f"   —     (n={n})"
    return out


def run(day: dt.date, want_trend: bool, surfaces: list | None,
        grace_min: int) -> tuple[int, dict]:
    now_utc = dt.datetime.now(dt.timezone.utc)
    report: dict = {'date': day.isoformat(),
                    'generated_at': now_utc.isoformat(timespec='seconds')}

    print(f"\n╔═══ DAILY PERFORMANCE · {day.isoformat()} (ET) ═══╗")
    print(f"    generated {now_utc.isoformat(timespec='seconds')} "
          f"· {(_et_today() - day).days}d back\n")

    # ── 1. is the day even complete? ──────────────────────────────────────
    print("1. IS THE DAY COMPLETE?  (a record off a half-graded night is fiction)")
    finals = finals_census(day, now_utc, grace_min)
    report['finals'] = finals
    hard_fail = False
    print(f"   {'SPORT':7s} {'GAMES':>5s} {'SCORED':>6s} {'MISSING':>7s}   status")
    for sport in sorted(finals):
        f = finals[sport]
        if f.get('error'):
            print(f"   {sport:7s}   QUERY FAILED — {f['error'][:70]}")
            hard_fail = True
            continue
        if not f['games']:
            continue
        if f['unscored'] == 0:
            status = 'complete'
        elif f['overdue']:
            status = f"🚨 OVERDUE — {f['note']}"
            hard_fail = True
        else:
            status = f"· waiting — {f['note']}"
        print(f"   {sport:7s} {f['games']:5d} {f['scored']:6d} "
              f"{f['unscored']:7d}   {status}")

    cells = pick_census(day, finals)
    report['picks'] = {f'{s}|{sp}': v for (s, sp), v in cells.items()}
    ungraded = {k: v for k, v in cells.items() if v['ungraded']}
    total_ung = sum(v['ungraded'] for v in ungraded.values())
    total_picks = sum(v['picks'] for v in cells.values())
    print(f"\n   published picks on {day.isoformat()}: {total_picks}"
          f" · ungraded {total_ung}")
    overdue_cells = {k: v for k, v in ungraded.items() if v['overdue']}
    for (surface, sport), v in sorted(ungraded.items(),
                                      key=lambda z: -z[1]['ungraded']):
        tag = ('🚨 OVERDUE — finals are all in'
               if v['overdue']
               else f"· waiting on {v['blocked_on_finals']} unscored final(s)")
        print(f"     {surface:14s} {sport:6s} ungraded "
              f"{v['ungraded']:4d} of {v['picks']:4d}   {tag}")
    if overdue_cells:
        hard_fail = True

    verdict = ('COMPLETE' if total_ung == 0 else
               'PROVISIONAL — numbers below will move')
    print(f"\n   VERDICT: {verdict}")
    report['verdict'] = verdict

    # ── 2. the day itself ─────────────────────────────────────────────────
    names = [s for s in C.SURFACES if (not surfaces or s in surfaces)]
    print(f"\n2. THE DAY — {len(names)} surfaces x {len(C.SPORTS)} sports")
    if not want_trend:
        print("   (--no-trend: day column only)")
    day_win = (day, day)
    wins = C._windows_today(_et_today())
    picked: dict = {}
    broke: list = []
    # The pickers are almost pure HTTP paging — prop_coverage alone walks
    # ~29k rows — so run them concurrently. Serial they take 2m45s, which is
    # too slow to sit inside the morning cadence, and the honest alternative
    # (dropping the prop tiers from the daily view) would hide the single
    # worst leak on the board. Each picker only reads and returns a list, so
    # there is no shared state to race; the one cost is that a picker's own
    # stdout can interleave, which is cosmetic.
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(C.SURFACES[n]): n for n in names}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                picked[name] = fut.result()
            except Exception as e:                            # noqa: BLE001
                # A picker that raised means the QUERY broke. Reporting it
                # as an empty surface would read as "no picks", which is the
                # lie this whole file exists to stop.
                broke.append((name, str(e)[:90]))
    if broke:
        hard_fail = True
        print("   🚨 PICKERS THAT RAISED (surface unreportable, NOT empty):")
        for n, e in broke:
            print(f"      {n}: {e}")

    header = f"   {'surface':16s} {'sport':6s} {'the day':>20s}"
    if want_trend:
        header += f"   {'d7':>30s}   {'d30':>30s}"
    rows_out: list = []
    for sport in C.SPORTS:
        block: list = []
        for name in names:
            rows = picked.get(name)
            if rows is None:
                continue
            d = C._aggregate(rows, sport, day_win)
            if not d:
                continue
            line = f"   {name:16s} {sport:6s} {_fmt_cell(d, False):>20s}"
            if want_trend:
                d7 = C._aggregate(rows, sport, wins['d7'])
                d30 = C._aggregate(rows, sport, wins['d30'])
                line += f"   {_fmt_cell(d7, True):>30s}   {_fmt_cell(d30, True):>30s}"
            block.append(line)
            rows_out.append({'surface': name, 'sport': sport, 'day': d})
        if block:
            print(f"\n  {sport}")
            print(header)
            for b in block:
                print(b)
    report['day_rows'] = rows_out
    if not rows_out:
        print("\n   no graded picks on any surface for this date")

    # ── 3. where the money is actually going ──────────────────────────────
    if want_trend:
        print("\n3. WHERE THE MONEY IS GOING — d30, n>=30 only, ranked by units")
        # NOT every surface here is money we actually put out. prop_coverage
        # and prop_lean are TIER UNIVERSES — every prop the pipeline scored
        # at that tier, 15,884 of them on NBA in 30 days — so their units
        # are notional and belong nowhere near a P&L. Reading -1316u as a
        # loss would be the same error as quoting a reconstruction as a live
        # record ([[feedback_surface_records_trust_levels]],
        # audit_live_surface_roi.py's opening note).
        #
        # The marker is deliberately coarse: COVERAGE and LEAN are certainly
        # not published, and the rest is a question this file should not
        # answer by guessing. v_*_props_publishable is where that line is
        # actually drawn, and wiring this board to it is the open item.
        print("   [notional] = a tier universe, not published picks — "
              "units are not P&L")
        leaks: list = []
        for name in names:
            rows = picked.get(name)
            if rows is None:
                continue
            for sport in C.SPORTS:
                a = C._aggregate(rows, sport, wins['d30'])
                if not a:
                    continue
                if (a['wins'] + a['losses']) < 30:
                    continue
                leaks.append((a['units_net'], name, sport, a))
        leaks.sort()
        if not leaks:
            print("   no (surface, sport) cell carries n>=30 over 30 days")
        _NOTIONAL = {'prop_coverage', 'prop_lean'}
        for units, name, sport, a in leaks[:12]:
            n = a['wins'] + a['losses']
            tag = ' [notional]' if name in _NOTIONAL else ''
            print(f"   {units:+8.1f}u  {name:16s} {sport:6s} "
                  f"{a['wins']}-{a['losses']}  {a['hit_rate'] * 100:5.1f}%  "
                  f"n={n}  roi {a['roi_pct']:+.1f}%{tag}")
        report['leaks'] = [{'surface': n, 'sport': s, 'units': u,
                            'hit_rate': a['hit_rate'],
                            'n': a['wins'] + a['losses']}
                           for u, n, s, a in leaks]

    code = 2 if hard_fail else (1 if total_ung else 0)
    print(f"\n╚═══ exit {code} "
          f"({'action required' if code == 2 else 'provisional' if code else 'clean'})"
          f" ═══╝\n")
    return code, report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='ET date to report (default: yesterday)')
    ap.add_argument('--no-trend', action='store_true',
                    help='skip d7/d30 columns and the leak board (faster)')
    ap.add_argument('--surface', action='append',
                    help='limit to this surface (repeatable)')
    ap.add_argument('--grace-min', type=int, default=DEFAULT_GRACE_MIN,
                    help=f'minutes past a resolver cron before an unscored '
                         f'final is a failure (default {DEFAULT_GRACE_MIN})')
    ap.add_argument('--json', action='store_true',
                    help='also emit the machine-readable report on stderr')
    args = ap.parse_args()

    day = (dt.date.fromisoformat(args.date) if args.date
           else _et_today() - dt.timedelta(days=1))
    unknown = [s for s in (args.surface or []) if s not in C.SURFACES]
    if unknown:
        print(f'unknown surface(s): {unknown}\n'
              f'known: {sorted(C.SURFACES)}')
        return 2
    code, report = run(day, not args.no_trend, args.surface, args.grace_min)
    if args.json:
        print(json.dumps(report, indent=2, default=str), file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main())
