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

COVERAGE, as well as population (added 2026-10-20, second pass). Population
alone could not tell that the right NUMBER of rows exist, so a slate missing
half its games read clean. Each sport's next slate is now counted against an
INDEPENDENT upstream schedule:

    MLB      statsapi.mlb.com          keyless
    NHL      api-web.nhle.com          keyless
    NFL      the Odds API              ODDS_API_KEY
    NCAAF    the Odds API              ODDS_API_KEY
    NBA      the Odds API              ODDS_API_KEY
    NCAAB    the Odds API              ODDS_API_KEY

    context 0 rows while upstream has games  -> FAIL  (wiring error, not a
                                                       finding - the builder
                                                       did not run)
    context short of upstream                -> WARN with both counts
    upstream unavailable                     -> say so; population still runs

That upgrades the old "no rows in the window" WARN into a FAIL when the
upstream says there was a slate to build, which is the one case where an empty
table is never acceptable.

UFC IS CHECKED DIFFERENTLY, because it has no context table at all - only
ufc_fight_results, which holds resolved fights. Its product is the read, so
the next card's jerry_reads rows are asserted instead. Price is deliberately
NOT asserted there: all 8 forward reads on the 10-24 card are call_market
'pass', and a pass legitimately carries no side and no price. Asserting it
would fail on correct behaviour. (The 8-of-8 pass rate is itself worth a look
and belongs to B83, not here.)

STILL NOT PROVEN: that the upstream itself is right, and that a row's VALUES
are correct rather than merely present. A wrong close_spread passes every check
in this file.

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

#: How far ahead each builder actually WRITES rows. A missing row beyond this
#: is not a gap — it is work that has not happened yet, and must not be
#: reported as a failure. MLB is 0 because game_context.py takes the ET window
#: ending 03:59Z tomorrow; football genuinely builds a week out.
BUILD_HORIZON = {'NFL': 9, 'NCAAF': 9, 'NCAAB': 9,
                 'MLB': 0, 'NHL': 3, 'NBA': 3}

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

ODDS_KEY = (os.environ.get('ODDS_API_KEY')
            or os.environ.get('THE_ODDS_API_KEY') or '')
ODDS_SPORT = {'NFL': 'americanfootball_nfl', 'NCAAF': 'americanfootball_ncaaf',
              'NBA': 'basketball_nba', 'NCAAB': 'basketball_ncaab'}
_odds_cache: dict = {}


def _et_date_of(iso: str) -> str:
    """ET calendar date of a UTC timestamp. Fixed -4 is WRONG after Nov 1 and
    a zone-aware conversion needs no extra dependency, so do it properly -
    the NCAAF slot chips shipped that exact bug today (327743af)."""
    try:
        t = dt.datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError:
        return ''
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    except Exception:
        return t.date().isoformat()


def upstream_count(sport: str, day: str):
    """How many games the OUTSIDE WORLD says exist on `day`.

    Returns an int, or None when the source is unavailable - None must never
    be read as zero, which is the whole reason this returns None rather than 0.
    """
    try:
        if sport == 'MLB':
            r = requests.get('https://statsapi.mlb.com/api/v1/schedule',
                             params={'sportId': '1', 'date': day}, timeout=30)
            if r.status_code != 200:
                return None
            return sum(len(d.get('games') or [])
                       for d in (r.json().get('dates') or []))

        if sport == 'NHL':
            r = requests.get(f'https://api-web.nhle.com/v1/schedule/{day}',
                             timeout=30)
            if r.status_code != 200:
                return None
            for wk in (r.json().get('gameWeek') or []):
                if str(wk.get('date')) == day:
                    return len(wk.get('games') or [])
            return 0

        key = ODDS_SPORT.get(sport)
        if not key or not ODDS_KEY:
            return None
        if key not in _odds_cache:
            # One call per sport per run, reused across dates.
            r = requests.get(
                f'https://api.the-odds-api.com/v4/sports/{key}/odds',
                params={'apiKey': ODDS_KEY, 'regions': 'us',
                        'markets': 'spreads', 'oddsFormat': 'american'},
                timeout=60)
            _odds_cache[key] = r.json() if r.status_code == 200 else None
        ev = _odds_cache[key]
        if ev is None or not isinstance(ev, list):
            return None
        return sum(1 for e in ev
                   if _et_date_of(e.get('commence_time')) == day)
    except Exception:                                       # noqa: BLE001
        return None


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
            and (str(x.get('sport')) in TABLE
                 or str(x.get('sport')) == 'UFC')]


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
        # ══ 2026-10-20 · ASK THE OUTSIDE WORLD BEFORE SHRUGGING ══
        # This used to be an unconditional WARN, which cannot tell a genuine
        # dead week (MLB between the ALCS and the World Series) from a builder
        # that never ran. Now the upstream schedule decides, and an empty table
        # against a real slate is a FAIL: 0 rows read is a wiring error, not a
        # finding.
        # ══ 2026-10-21 · PROBE ONLY AS FAR AS THE BUILDER REACHES ══
        # First run of the coverage branch reported "MLB has NO rows for
        # 2026-10-23, upstream lists 1 game" — World Series Game 1. True, and
        # a FALSE ALARM: game_context.py is same-day by construction, so a
        # game two days out is not missing, it is not built yet. Left as-is
        # this would have fired every day until the Series started, which is
        # how a check earns a mute.
        #
        # HORIZON_DAYS says how far to LOOK for a slate; BUILD_HORIZON says
        # how far the builder actually WRITES. Only the second is grounds for
        # calling a gap a failure.
        hz = min(HORIZON_DAYS.get(sport, 7), BUILD_HORIZON.get(sport, 7))
        for i in range(hz + 1):
            d = (today + dt.timedelta(days=i)).isoformat()
            up = upstream_count(sport, d)
            if up:
                return ([f'{sport}: {table} has NO rows for {d}, but the '
                         f'upstream schedule lists {up} game(s) — the context '
                         f'builder did not run'], notes)
            if up is None:
                notes.append(f'{sport}: upstream schedule unavailable for {d}'
                             ' — cannot confirm a dead week')
                break
        return [], notes + [
            f'{sport}: no rows in the next {hz} days, and the upstream '
            f'schedule agrees there are no games — dead week']

    rows_all = list(rows)       # before the started-game filter, for coverage
    slate_underway = False

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
        slate_underway = started > 0
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

    # ══ COVERAGE · ONLY WHERE THE TWO SIDES ARE COMPARABLE ══
    # First version reported NHL "3/11 SHORT 8" and NBA "3/2", both artefacts
    # of mixing two different populations:
    #
    #   our side      had started games REMOVED (correctly - a started game's
    #                 row can no longer be written, so population must ignore
    #                 it)
    #   upstream      counts the whole day for MLB/NHL, and for the Odds API
    #                 DROPS games once they begin
    #
    # So on a day already underway the comparison is meaningless in both
    # directions, and a number nobody can act on is worse than no number.
    # Coverage therefore runs only on a slate where nothing has started, and
    # counts every row we hold for that date (rows_all), not the filtered
    # slate. On an untouched future date the two sides mean the same thing.
    held = sum(1 for x in rows_all if str(x['game_date']) == slate_date)
    # And the same caution where we CANNOT tell what has started: MLB and NBA
    # context carry no start-time column at all, so on today's date there is
    # no way to know which games are in progress - and the Odds API has
    # already dropped the ones that are. NBA read "3/2 upstream" for exactly
    # that reason. Coverage on a future date is unaffected and is the case
    # that matters, since that is where a builder gap shows up.
    if slate_date == et_today().isoformat() and (slate_underway or not tcol):
        why = ('slate already underway' if slate_underway
               else 'no start-time column, so in-progress games cannot be '
                    'identified')
        notes.append(f'    coverage           {why} — not comparable, skipped')
    else:
        up = upstream_count(sport, slate_date)
        if up is None:
            notes.append('    coverage           upstream unavailable — '
                         'population only')
        elif up == 0:
            notes.append(f'    coverage           upstream lists 0 while we '
                         f'hold {held} — check for a game it has dropped')
        elif held < up:
            notes.append(f'    coverage           {held}/{up} upstream  '
                         f'SHORT {up - held}')
        else:
            notes.append(f'    coverage           {held}/{up} upstream  ok')

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


def check_ufc() -> tuple:
    """UFC has no *_game_context. Its product is the read, so assert the read.

    ufc_fight_results holds RESOLVED fights only (0 forward rows), so there is
    no slate table to check. jerry_reads is what ships, and what a user sees.

    PRICE IS NOT ASSERTED, deliberately. All 8 forward reads on the 10-24 card
    are call_market 'pass', and a pass carries no side and no price by design -
    asserting it would fail on correct behaviour. The pass RATE is reported
    because 8 of 8 is worth a human look (B83), but it is a note, not a fail.
    """
    today = et_today()
    r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H, params=[
        ('select', 'game_date,game_id,short_read,call_market,conviction'),
        ('sport', 'eq.UFC'),
        ('game_date', f'gte.{today.isoformat()}'),
        ('order', 'game_date.asc'),
        ('limit', '200'),
    ], timeout=60)
    if r.status_code != 200:
        return ([f'UFC: jerry_reads fetch {r.status_code} — {r.text[:160]}'],
                [])
    rows = r.json()
    if not rows:
        return [], ['UFC: no forward reads — no card inside the window, or '
                    'the read generator has not run (no upstream card feed '
                    'wired, so this cannot be told apart yet)']

    card_date = min(str(x['game_date']) for x in rows)
    card = [x for x in rows if str(x['game_date']) == card_date]
    notes = [f'UFC: next card {card_date}, {len(card)} fights with reads']
    fails = []
    for col in ('short_read', 'call_market', 'conviction'):
        n = sum(1 for x in card if x.get(col) not in (None, ''))
        mark = 'ok  ' if n == len(card) else ('DARK' if n == 0 else 'part')
        notes.append(f'    {col:<18} {n:3d}/{len(card):<3d} {mark}')
        if n == 0:
            fails.append(f'UFC {card_date}: {col} is 0 of {len(card)} — a '
                         f'column that should be full is EMPTY')
    passes = sum(1 for x in card
                 if str(x.get('call_market') or '').lower() == 'pass')
    notes.append(f'    passes             {passes:3d}/{len(card):<3d} '
                 + ('— the engine has no opinion on this card (B83)'
                    if passes == len(card) else ''))
    return fails, notes


#: Feeds that silently going quiet would not show up as a missing COLUMN.
#: (table, timestamp column, max age in hours, sport or None for cross-sport)
#:
#: Ages are deliberately generous — roughly twice the expected cadence — so a
#: late cron does not trip this. It fires on a feed that has genuinely stopped,
#: which is the only thing it is for.
_FEEDS = [
    ('nba_injuries',       'updated_at',       40, 'NBA'),
    ('nfl_injuries',       'updated_at',       72, 'NFL'),
    ('ncaaf_injuries',     'updated_at',       72, 'NCAAF'),
    ('nba_team_stats',     'updated_at',       72, 'NBA'),
    ('line_snapshot',      'snapshot_ts',      40, None),
    ('team_stats_rolling', 'refreshed_at',     72, None),
    ('jerry_reads',        'generated_at',     40, None),
    ('signal_registry',    'last_computed_at', 72, None),
    ('external_picks',     'pulled_at',        60, None),
]


def check_feed_recency(in_season: list) -> tuple:
    """Has each feed table been WRITTEN recently enough to be trusted.

    ══ 2026-10-21 · POPULATION IS NOT FRESHNESS ══
    The slate check above proves the rows present are filled in. It cannot
    prove they are CURRENT, and that gap has now cost twice:

      * nba_injuries went SEVEN DAYS stale while nba_pipeline ran daily, so
        the card rendered "won't play in Thursday's PRESEASON game" under a
        regular-season matchup. Every ctx column it feeds was populated, so
        nothing upstream noticed.
      * NCAAF weather was dark in CI for five weeks (B107) for the same
        reason — the step was green, the table had rows, the rows were old.

    Both were invisible because the workflow steps carry continue-on-error AND
    do not run through run_step.sh, so a failure writes no step_failed
    heartbeat either. A sweep found 311 steps with that exact shape. Fixing
    311 steps is not the answer; asking whether the OUTPUT moved is, because
    one check covers every one of them and every one added later.

    Only in-season sports are asserted — an NHL feed in July is correctly
    quiet, and a check that fires all summer gets muted by August.
    """
    now = dt.datetime.now(dt.timezone.utc)
    fails, notes = [], []
    for table, col, max_h, sport in _FEEDS:
        if sport and sport not in in_season:
            continue
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                         params={'select': col, 'order': f'{col}.desc',
                                 'limit': '1'}, timeout=30)
        if r.status_code != 200:
            # A 404/400 here means the table or the column is not what this
            # list claims. That is a defect in the list, and silently skipping
            # it is how the list rots — say so.
            notes.append(f'    {table:<20} HTTP {r.status_code} on {col} '
                         f'— cannot check, fix _FEEDS')
            continue
        rows = r.json()
        if not rows or not rows[0].get(col):
            notes.append(f'    {table:<20} empty — nothing written ever')
            continue
        raw = str(rows[0][col])
        try:
            t = dt.datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            notes.append(f'    {table:<20} unparseable timestamp {raw[:24]}')
            continue
        age = (now - t).total_seconds() / 3600.0
        mark = 'ok  ' if age <= max_h else 'STALE'
        notes.append(f'    {table:<20} {age:7.1f}h old  (limit {max_h}h)  {mark}')
        if age > max_h:
            fails.append(f'{table}: last written {age:.0f}h ago, limit {max_h}h '
                         f'— the step that writes it is failing silently')
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
        if s not in TABLE and s != 'UFC':
            print(f'{s}: no context table mapped — skipped')
            continue
        fails, notes = check_ufc() if s == 'UFC' else check_sport(s)
        for line in notes:
            print(line)
        all_fails += fails
        print()

    # Feeds: population above proves rows are filled, this proves they moved.
    print('feed recency')
    _ffails, _fnotes = check_feed_recency(sports)
    for line in _fnotes:
        print(line)
    all_fails += _ffails
    print()

    if all_fails:
        print('*** DARK COLUMNS OR STALE FEEDS — something is failing green ***')
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
