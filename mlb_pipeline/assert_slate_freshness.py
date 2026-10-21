#!/usr/bin/env python3
"""Assert that the next slate actually HAS the data it cannot ship without.

Opened by B107 (2026-10-20). NCAAF weather was dark in CI for five weeks -
09-26, 10-03 and 10-17, 171 games - and every signal we had said the pipeline
was fine:

  * every cron slot ran: workflow_heartbeat logged start AND end
  * every step was green: the weather step carries continue-on-error, and the
    script returned 1 before doing any work, so Actions showed a pass
  * the table had rows: ncaaf_game_context held all 52 Saturday games

What nobody checked was whether a column that should be full was EMPTY. That
is the gap this closes. ~400 workflow steps carry continue-on-error (140 of
177 in mlb_pipeline.yml alone), so step exit codes cannot be the signal;
heartbeat answers "did the workflow run", which was true throughout.

WHAT IT CHECKS, and why only this

A "dark column" - zero populated rows out of N on a slate that HAS rows - is
the B107 signature and is almost never legitimate. Partial population is NOT
a finding: football context fills in across the week (ncaaf_game_context held
14 of Saturday's 44 games on the Tuesday and closed at 52/52 the week before),
so failing on partials would cry wolf every Tuesday and get muted. So:

    rows == 0 on every forward date   -> WARN  (dead week, or a real outage;
                                                heartbeat covers the latter)
    column 0 of N on the next slate   -> FAIL  (something returned early)
    column k of N, 0 < k < N          -> note  (progressive fill, expected)

It checks THE NEXT SLATE ONLY - the nearest game_date at or after today that
has rows - because that is the one date where everything should already be
populated, which keeps the test sharp enough to act on.

Weather is asserted on OUTDOOR rows only, read from each sport's own column
(`roof` on NFL, `dome` on NCAAF), because a dome legitimately has no forecast
and MLB/NHL/NBA have no weather columns at all.

COLUMNS ARE DISCOVERED, NOT DECLARED. Every explicit select in this repo that
named a column which did not exist returned 400, which the callers read as
"no data" - the trap in feedback_explicit_select_silent_blanks, hit again on
kickoff_utc and close_spread this month. So this reads one row with `select=*`,
intersects the candidate list with what is really there, and reports what it
could not check instead of guessing. A phantom column here would make the
guard itself the outage.

WHAT THIS DOES NOT PROVE. It asserts POPULATION, not COVERAGE: it can tell
that the rows present are filled in, and cannot tell that the right number of
rows exist. A slate missing half its games looks clean here. Proving coverage
needs each sport's upstream schedule (MLB statsapi, CFBD, the Odds API), which
is a separate check and a separate set of credentials. Said plainly so nobody
reads a green run as "the slate is complete".

Usage:
  python assert_slate_freshness.py              # every in_season sport
  python assert_slate_freshness.py --sport NCAAF
  python assert_slate_freshness.py --warn-only  # never exit non-zero
"""
import argparse
import os
import sys
import datetime as dt
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']
H = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}

# How far ahead to look for the next slate. Football is weekly, so a Tuesday
# run must be able to see Saturday; the daily sports should always have
# something within a couple of days.
# 2026-10-20, and the reasoning is worth keeping because the first two
# attempts were both wrong. MLB started at 3 days and reported "no rows in the
# next 3 days", which was true and nearly misleading - the ALCS ended 10-19 and
# the WORLD SERIES opens 10-23, a gap no 3-day window sees across. Raising it
# to 6 did not help either: MLB has no rows at 6 days out and SHOULD NOT. The
# builder is same-day by construction (game_context.py takes the ET window
# ending 03:59Z tomorrow), so the World Series rows appear on 10-23 itself.
# A horizon longer than the builder's reach manufactures a permanent warning,
# which is how a check gets muted. So MLB is 2 - today plus the late-night
# roll - and the football horizons stay long because football IS built a week
# out.
HORIZON_DAYS = {'NFL': 9, 'NCAAF': 9, 'NCAAB': 9,
                'MLB': 2, 'NHL': 3, 'NBA': 3}

# Candidate columns per sport. Intersected with the live schema before use.
#   market   — the number every published pick is priced against
#   engine   — what the engine decided
#   weather  — outdoor football only
CHECKS = {
    'market': ['close_spread', 'close_total'],
    'engine': ['primary_play', 'sweat_score', 'projected_spread',
               'projected_total'],
    'weather': ['temp', 'wind'],
}
# Sports with no weather columns at all — asserting them would be a false fail.
NO_WEATHER = {'MLB', 'NHL', 'NBA', 'NCAAB'}

# ══ 2026-10-20 · A STARTED GAME IS NOT A FINDING ══
# First run of this script flagged NCAAF temp 0/3 on today's slate. It was a
# FALSE POSITIVE and worth more than the catch: those three games had already
# kicked off, and the weather puller's window is `kickoff_utc >= now`, so a
# game in progress is correctly excluded from it. Nothing can fix a started
# game's row, so asserting on one only teaches us to ignore this script.
# Where the sport has a start-time column, rows that have already begun are
# dropped and the next slate is taken from what is left.
TIME_COL = {'NFL': 'kickoff_utc', 'NCAAF': 'kickoff_utc',
            'NCAAB': 'kickoff_utc', 'NHL': 'commence_time'}

TABLE = {s: f'{s.lower()}_game_context'
         for s in ('MLB', 'NFL', 'NCAAF', 'NHL', 'NBA', 'NCAAB')}


def et_today() -> dt.date:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(hours=4)).date()


def live_columns(table: str) -> set:
    """What this table ACTUALLY has. See the module docstring on why."""
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                     params={'select': '*', 'limit': '1'}, timeout=30)
    if r.status_code != 200:
        return set()
    rows = r.json()
    return set(rows[0].keys()) if rows else set()


def in_season_sports() -> list:
    r = requests.get(f'{SB}/rest/v1/sport_registry', headers=H,
                     params={'select': 'sport,state'}, timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ sport_registry {r.status_code} — checking all sports')
        return sorted(TABLE)
    return [str(x['sport']) for x in r.json()
            if str(x.get('state') or '').lower() == 'in_season'
            and str(x.get('sport')) in TABLE]


def outdoor(row: dict) -> bool:
    """True when this venue should have a forecast."""
    if row.get('dome') is True:
        return False
    roof = str(row.get('roof') or '').lower()
    if roof in ('dome', 'closed', 'indoor', 'indoors', 'retractable-closed'):
        return False
    return True


def check_sport(sport: str) -> tuple:
    """Returns (fails, notes) — fails are dark columns on the next slate."""
    table = TABLE[sport]
    cols = live_columns(table)
    if not cols:
        return [], [f'{sport}: {table} is empty or unreadable — nothing to '
                    f'assert (heartbeat covers a dead pipeline)']

    wanted = ['game_date']
    for group, names in CHECKS.items():
        if group == 'weather' and sport in NO_WEATHER:
            continue
        wanted += [c for c in names if c in cols]
    for c in ('dome', 'roof'):
        if c in cols:
            wanted.append(c)
    tcol = TIME_COL.get(sport)
    if tcol and tcol in cols:
        wanted.append(tcol)
    else:
        tcol = None
    wanted = list(dict.fromkeys(wanted))

    today = et_today()
    horizon = today + dt.timedelta(days=HORIZON_DAYS.get(sport, 7))
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=[
        ('select', ','.join(wanted)),
        ('game_date', f'gte.{today.isoformat()}'),
        ('game_date', f'lte.{horizon.isoformat()}'),
        ('order', 'game_date.asc'),
        ('limit', '1000'),
    ], timeout=60)
    if r.status_code != 200:
        # A 400 here means a name in `wanted` is not real, which cannot happen
        # now that they are intersected with the live schema — so it is worth
        # shouting about rather than swallowing.
        return ([f'{sport}: context fetch {r.status_code} — {r.text[:160]}'],
                [])

    rows = r.json()
    fails, notes = [], []
    if not rows:
        return [], notes + [
            f'{sport}: no rows in the next {HORIZON_DAYS.get(sport, 7)} days '
            f'— dead week, or the context builder has not run']

    # Drop anything already underway — see the TIME_COL note above.
    if tcol:
        now = dt.datetime.now(dt.timezone.utc)
        kept = []
        for x in rows:
            raw = str(x.get(tcol) or '')
            if not raw:
                kept.append(x)          # no time to judge by; keep it
                continue
            try:
                t = dt.datetime.fromisoformat(raw.replace('Z', '+00:00'))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                kept.append(x)
                continue
            if t > now:
                kept.append(x)
        started = len(rows) - len(kept)
        rows = kept
        if started:
            notes.append(f'{sport}: {started} game(s) already underway — '
                         f'excluded, their rows can no longer be written')
        if not rows:
            return [], notes + [f'{sport}: every game in the window has '
                                f'started — nothing to assert']

    # The NEXT SLATE only: the nearest date that has rows. Later dates are
    # legitimately half-built (see the docstring).
    slate_date = min(str(x['game_date']) for x in rows)
    slate = [x for x in rows if str(x['game_date']) == slate_date]

    notes.append(f'{sport}: next slate {slate_date}, {len(slate)} games')

    for group, names in CHECKS.items():
        if group == 'weather' and sport in NO_WEATHER:
            continue
        for col in names:
            if col not in cols:
                notes.append(f'    {col:<18} not a column on {table} — skipped')
                continue
            pool = [x for x in slate if outdoor(x)] if group == 'weather' \
                else slate
            if not pool:
                notes.append(f'    {col:<18} no outdoor games on this slate')
                continue
            n = sum(1 for x in pool if x.get(col) not in (None, ''))
            mark = 'ok  ' if n == len(pool) else ('DARK' if n == 0 else 'part')
            notes.append(f'    {col:<18} {n:3d}/{len(pool):<3d} {mark}')
            if n == 0:
                fails.append(
                    f'{sport} {slate_date}: {col} is 0 of {len(pool)} — '
                    f'a column that should be full is EMPTY')
    return fails, notes


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--sport')
    p.add_argument('--warn-only', action='store_true',
                   help='report but always exit 0')
    a = p.parse_args()

    print('== slate freshness ==')
    sports = [a.sport.upper()] if a.sport else in_season_sports()
    print(f'  checking: {", ".join(sports)}\n')

    all_fails = []
    for s in sports:
        if s not in TABLE:
            print(f'{s}: no context table mapped — skipped')
            continue
        fails, notes = check_sport(s)
        for line in notes:
            print(line)
        all_fails += fails
        print()

    if all_fails:
        print('*** DARK COLUMNS — something returned early and went green ***')
        for f in all_fails:
            print(f'  {f}')
        print('\n  This is the B107 shape. Check the step that writes that '
              'column: it almost certainly exited before doing any work while '
              'continue-on-error kept the run green.')
        return 0 if a.warn_only else 1

    print('all checked columns populated on each next slate')
    return 0


if __name__ == '__main__':
    sys.exit(main())
