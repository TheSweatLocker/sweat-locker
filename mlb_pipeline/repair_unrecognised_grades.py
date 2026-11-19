#!/usr/bin/env python3
"""Repair result grades that are present but not a value any reader accepts.

WHY THIS EXISTS
---------------
2026-11-19, found by audit_situational_records. ncaaf_game_results carries
spread_result = 'true' on 3 rows and 'false' on 1:

    2026-09-05  Youngstown St Penguins @ Kentucky   13-45   -23.5  'true'
    2026-09-05  Houston Baptist Huskies @ Rice       3-31   -21.5  'true'
    2026-09-05  LIU Sharks @ Kansas                  6-51   -39.5  'true'
    2026-09-05  FIU @ South Florida                  9-19   -13.5  'false'

Booleans in a text result column. They are WORSE than NULL, and in a
specific way: team_situational_records lists the exact strings its CASE
accepts and sends everything else to NULL, so these games are GRADED in the
table and ABSENT from the record. Every count that reads the grade column
disagrees with every count that reads the table, and neither looks broken.
That is the 4-game NCAAF shortfall the audit reports.

backfill_game_grades deliberately will not fix them: its contract is "only
fill a grade that is NULL", which is the right contract — a script that
rewrites existing grades can quietly launder a bad line into a bad record.
So the repair is separate, narrower, and loud about every row it changes.

TWO CLASSES, TREATED DIFFERENTLY ON PURPOSE
  UNRECOGNISED  a value no reader accepts ('true', 'false', ''). There is
                no interpretation to lose, so --apply replaces it with the
                grade recomputed from score + line.
  DISAGREEING   a valid value that contradicts score + line. Reported, and
                only written with --fix-disagreements, because the other
                possibility is that the stored LINE is wrong and the grade
                is right — overwriting then launders a bad line into a
                plausible grade. One row qualifies today (UAB @ Louisiana
                2026-09-19, 14-21 on -6.5: the home side won by 7 and
                covered, stored as away_covered) and the arithmetic there
                is not in doubt, but the flag stays separate so the next
                occurrence gets looked at rather than swept.

NOT a grader. grade_spread/grade_total are imported from
backfill_game_grades so there is exactly one implementation of "who
covered" in this repo. Two implementations is how NHL and NCAAB came to
disagree in the first place.

CLI
  python repair_unrecognised_grades.py --sport NCAAF
  python repair_unrecognised_grades.py --sport NCAAF --apply
  python repair_unrecognised_grades.py --sport NCAAF --apply --fix-disagreements
  python repair_unrecognised_grades.py --all
  python repair_unrecognised_grades.py --self-test
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

import requests

from backfill_game_grades import SPORTS, grade_spread, grade_total

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        _ln = _ln.strip()
        if _ln and not _ln.startswith('#') and '=' in _ln:
            _k, _v = _ln.split('=', 1)
            os.environ.setdefault(_k, _v.strip().strip('"'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
HDRS = {'apikey': KEY or '', 'Authorization': 'Bearer ' + (KEY or '')}
WHDRS = {**HDRS, 'Content-Type': 'application/json',
         'Prefer': 'return=representation'}

#: Exactly the strings team_situational_records' CASE accepts. Anything else
#: it maps to NULL, which is the whole defect, so this list is the contract
#: and must not be loosened to "whatever the data happens to contain".
OK_SPREAD = {'home_covered', 'home_cover', 'away_covered', 'away_cover',
             'push'}
OK_TOTAL = {'over', 'under', 'push'}

#: NFL stores close_spread as a home MARGIN (positive = home favoured);
#: every other sport stores a home HANDICAP. grade_spread takes a handicap,
#: so NFL would need negating. It is omitted rather than guessed at, and the
#: omission is asserted in the self-test so adding it later is a decision
#: rather than an accident.
#:
#: MLB is absent for a different reason, and the self-test caught me putting
#: it here: it is not in backfill_game_grades.SPORTS at all, so there is no
#: band or column config to repair against. Its spread column is the run
#: line, fixed at +/-1.5 like NHL's puck line, and it would need its own
#: entry there first. Every name here must exist in SPORTS — asserted below,
#: because a target with no config fails at the first row rather than at
#: import, which is the worst time to find out.
HANDICAP_SPORTS = {'NCAAF', 'NCAAB', 'NHL', 'NBA'}


def _page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = '1000'
        p['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=HDRS, params=p,
                         timeout=180)
        if r.status_code not in (200, 206):
            raise SystemExit(f'! {table} {r.status_code} {r.text[:200]}')
        chunk = r.json()
        if not isinstance(chunk, list):
            raise SystemExit(f'! {table} returned {chunk}')
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def classify(stored, expected, ok_values):
    """'ok' | 'unrecognised' | 'disagree' | 'ungradeable'."""
    if expected is None:
        return 'ungradeable'
    s = (stored or '').strip().lower()
    if not s:
        return 'ungradeable'          # NULL is backfill_game_grades' job
    if s not in ok_values:
        return 'unrecognised'
    norm = {'home_cover': 'home_covered', 'away_cover': 'away_covered'}
    return 'ok' if norm.get(s, s) == expected else 'disagree'


def run(sport, apply_, fix_dis):
    cfg = SPORTS.get(sport)
    if not cfg:
        print(f'! {sport} is not in backfill_game_grades.SPORTS')
        return 2
    if sport not in HANDICAP_SPORTS:
        print(f'! {sport} does not store a home HANDICAP in '
              f"{cfg['spread_col']} — refusing rather than guessing the "
              f'sign. See HANDICAP_SPORTS.')
        return 2
    rows = _page(cfg['results'],
                 {'select': f"game_id,game_date,home_team,away_team,"
                            f"home_score,away_score,spread_result,"
                            f"total_result,{cfg['spread_col']},"
                            f"{cfg['total_col']},{cfg['scored_col']}"})
    scored = [r for r in rows if r.get('home_score') is not None
              and r.get('away_score') is not None]
    print(f'== repair_unrecognised_grades · {sport} · {len(scored)} scored '
          f'rows ==')
    todo, dis = [], []
    tally = collections.Counter()
    for r in scored:
        exp_s = grade_spread(r['home_score'], r['away_score'],
                             r.get(cfg['spread_col']))
        # grade_total takes the two SCORES, not a precomputed total — it
        # sums them itself. Passing the stored total column as the first
        # argument is a TypeError, which is how I found this; passing a
        # stored total_points as "home" would have been worse, because it
        # would have graded silently against a doubled number.
        exp_t = grade_total(r['home_score'], r['away_score'],
                            r.get(cfg['total_col']))
        for col, stored, exp, ok in (
                ('spread_result', r.get('spread_result'), exp_s, OK_SPREAD),
                ('total_result', r.get('total_result'), exp_t, OK_TOTAL)):
            verdict = classify(stored, exp, ok)
            tally[(col, verdict)] += 1
            if verdict == 'unrecognised':
                todo.append((r, col, stored, exp))
            elif verdict == 'disagree':
                dis.append((r, col, stored, exp))
    print(f'  {dict(sorted((f"{c}:{v}", n) for (c, v), n in tally.items()))}')

    def _show(label, items):
        if not items:
            return
        print(f'\n  {label}: {len(items)}')
        for r, col, stored, exp in items:
            print(f"    {str(r['game_date'])[:10]}  "
                  f"{str(r['away_team'])[:24]:24s} @ "
                  f"{str(r['home_team'])[:24]:24s} "
                  f"{r['away_score']}-{r['home_score']}  "
                  f"line {r.get(SPORTS[sport]['spread_col'])}  "
                  f"{col}: {stored!r} -> {exp!r}")

    _show('UNRECOGNISED (no interpretation to lose)', todo)
    _show('DISAGREEING (needs --fix-disagreements)', dis)

    writes = list(todo) + (list(dis) if fix_dis else [])
    if not writes:
        print('\n  nothing to repair.')
        return 0
    if not apply_:
        print(f'\n  DRY RUN — would patch {len(writes)} value(s). '
              f'Re-run with --apply.')
        return 0
    ok = bad = 0
    for r, col, stored, exp in writes:
        resp = requests.patch(
            f"{SB}/rest/v1/{cfg['results']}", headers=WHDRS,
            params={'game_id': f"eq.{r['game_id']}"},
            data=json.dumps({col: exp}), timeout=60)
        if resp.status_code in (200, 201, 204) and resp.json():
            ok += 1
        else:
            bad += 1
            print(f"    ! {r['game_id']} {col} {resp.status_code} "
                  f"{resp.text[:120]}")
    print(f'\n  repaired {ok} · failed/no-op {bad}')
    return 0 if bad == 0 else 1


def _self_test():
    cases = []
    # The live NCAAF rows, as data.
    cases.append(("'true' is unrecognised, not a disagreement",
                  classify('true', 'home_covered', OK_SPREAD)
                  == 'unrecognised'))
    cases.append(("'false' is unrecognised",
                  classify('false', 'away_covered', OK_SPREAD)
                  == 'unrecognised'))
    cases.append(('a correct grade is ok',
                  classify('home_covered', 'home_covered', OK_SPREAD)
                  == 'ok'))
    cases.append(('the short alias home_cover is ok, not a disagreement',
                  classify('home_cover', 'home_covered', OK_SPREAD) == 'ok'))
    cases.append(('a valid but wrong grade is a disagreement',
                  classify('away_covered', 'home_covered', OK_SPREAD)
                  == 'disagree'))
    cases.append(('NULL is left to backfill_game_grades',
                  classify(None, 'home_covered', OK_SPREAD) == 'ungradeable'))
    cases.append(('no expected grade -> ungradeable, never a repair',
                  classify('true', None, OK_SPREAD) == 'ungradeable'))
    cases.append(('over/under use their own vocabulary',
                  classify('home_covered', 'over', OK_TOTAL)
                  == 'unrecognised'))
    # The UAB @ Louisiana row: 14-21 on a -6.5 home handicap. Home won by 7.
    cases.append(('UAB @ Louisiana regrades to home_covered',
                  grade_spread(21, 14, -6.5) == 'home_covered'))
    # A Kentucky-style blowout: home 45, away 13, home -23.5 -> covered.
    cases.append(('a -23.5 home favourite winning by 32 covers',
                  grade_spread(45, 13, -23.5) == 'home_covered'))
    # grade_total's signature is (home, away, total) — it sums the scores
    # itself. Asserting it here because calling it with a precomputed total
    # is the mistake I actually made, and the shape that would NOT raise
    # (a stored total_points passed as `home`) grades against a doubled
    # number without complaining.
    cases.append(('grade_total(28, 24, 48.5) is over',
                  grade_total(28, 24, 48.5) == 'over'))
    cases.append(('grade_total(10, 14, 48.5) is under',
                  grade_total(10, 14, 48.5) == 'under'))
    cases.append(('grade_total pushes on the number',
                  grade_total(24, 24, 48.0) == 'push'))
    # NFL must stay out until someone negates its sign deliberately.
    cases.append(('NFL is excluded from HANDICAP_SPORTS',
                  'NFL' not in HANDICAP_SPORTS))
    cases.append(('every HANDICAP_SPORTS entry exists in SPORTS',
                  all(s in SPORTS for s in HANDICAP_SPORTS)))
    fails = 0
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            fails += 1
    print(f'\n  {len(cases) - fails}/{len(cases)} self-test cases passed')
    return 1 if fails else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--fix-disagreements', action='store_true')
    ap.add_argument('--self-test', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        raise SystemExit(_self_test())
    targets = sorted(HANDICAP_SPORTS) if a.all else [a.sport]
    if not targets or targets == [None]:
        raise SystemExit('give --sport or --all')
    rc = 0
    for sp in targets:
        rc = max(rc, run(sp, a.apply, a.fix_disagreements))
        print()
    raise SystemExit(rc)
