"""One definition of "what day is it" and "what day is this game".

══ WHY THIS EXISTS ══
The ET conversion is reimplemented across this codebase under at least
five different names — _et_today, _today_et, today_et, _et_now, et_now —
in 214 files, and a handful of modules skip it entirely and call
date.today(). That is not a style problem. It has produced the same bug
three separate times:

  * nfl_generate_props, 2026-09-19: `game_date` sliced the UTC date off
    commence_time, filing 164 of 1,233 props (13%) on dates with no NFL
    games at all — every one the day AFTER a real slate. Fixed with
    _et_game_date.
  * nfl_generate_props, 2026-10-12: the ctx LOOKUP kept the raw UTC slice
    after the field was fixed, so every TNF/SNF/MNF game found no context
    row. Primetime props scored with no weather, rest, game_script or
    opponent-tier signals, and the anytime-TD de-vig fell back to
    devig='none' on the whole slate.
  * nfl_generate_props, same day: the early-season tier cap compared a UTC
    slice against a cutoff date, so a primetime game on the ET evening of
    09-21 escaped the cap.

Each was fixed in place and the next copy kept the bug, because there was
nothing to import.

══ THE RULE ══
Sports days are ET days. A game kicking 8:15pm ET on the 12th belongs to
the 12th, even though it is 00:15 UTC on the 13th. Every game_date column
in this project stores the ET play date, so anything comparing a timestamp
to a game_date must convert first.

date.today() is especially dangerous because it is LOCAL: it returns the
ET date on Andy's Windows box and the UTC date on a GitHub runner, so the
same code is correct in dev and wrong in CI for four hours a night.

ET was UTC-4 here year-round on purpose. The reasoning was that these
functions only decide which calendar day a game belongs to, and a
one-hour error cannot move a game across midnight unless it kicks between
11pm and midnight ET — which no slate does.

2026-10-21: that holds for a GAME time and not for a JOB time. et_today()
is asked what day it is from inside a cron, and mlb_grade_overnight fires
at 04:10 UTC — which is 00:10 ET under the -4 offset and 23:10 the
PREVIOUS day once EST starts on 2026-11-01. Precisely the 11pm-midnight
case, every single night, on a job that writes grades.

So et_now/et_today now use zoneinfo and get DST right; the -4 constant
stays as the fallback for a box with no tzdata, and as the offset the
game-date helpers below were measured against.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

#: Hours behind UTC. See the module docstring for why this is a constant.
ET_OFFSET_HOURS = 4


def et_shift() -> _dt.timedelta:
    """How far UTC is AHEAD of ET right now -- 4h in EDT, 5h in EST.

    == 2026-11-03 . 270 FILES SUBTRACT A HARDCODED 4 HOURS ===============
    et_now() above was made DST-correct on 10-21, but 270 files never
    imported it and still compute their own day as

        (datetime.now(timezone.utc) - timedelta(hours=4)).date()

    Measured by sweeping every UTC hour of 2026: that expression disagrees
    with the true ET date in exactly ONE hour -- **04:00-04:59 UTC, on 127
    days a year** (the EST half). Outside that hour it is byte-identical, so
    swapping in this function changes nothing except inside the broken hour,
    where the hardcoded version is a DAY AHEAD.

    That hour is not idle. `mlb_grade_overnight` fires a cron at **04:10
    UTC** and `mlb_prop_calibration` at **04:30 UTC**, and the nightly chain
    the first one runs includes grade_potd, grade_public_receipts,
    aggregate_daily_records, void_orphaned_picks and verify_potd_coverage --
    graders and void sweeps, on a day-ahead date. That is precisely
    feedback_window_from_runner_clock_is_a_day_ahead: "a day-ahead today
    reached into games still being PLAYED and swept them as finished."

    Returned as a timedelta, not a datetime, so a call site changes only
    `timedelta(hours=4)` -> `et_shift()` and keeps its tz-awareness and
    whatever it does next (.date(), .strftime(), .date().isoformat()).

    Falls back to the 4-hour constant if tzdata is unavailable, same as
    et_now().
    """
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        off = now_utc.astimezone(ZoneInfo('America/New_York')).utcoffset()
        if off is not None:
            return -off
    except Exception:                                        # noqa: BLE001
        pass
    return _dt.timedelta(hours=ET_OFFSET_HOURS)


def et_now() -> _dt.datetime:
    """Current wall-clock time in ET, naive — DST-correct.

    zoneinfo rather than ET_OFFSET_HOURS, because the hour this returns
    decides the DATE for a 04:10 UTC cron and that flips on 2026-11-01.
    Falls back to the constant only if tzdata is unavailable.
    """
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo('America/New_York')).replace(
            tzinfo=None)
    except Exception:
        return now_utc.replace(tzinfo=None) - _dt.timedelta(
            hours=ET_OFFSET_HOURS)


def et_today() -> str:
    """Today's ET date as YYYY-MM-DD.

    Use this instead of date.today() anywhere the result is compared to a
    game_date, or the job silently looks at the wrong slate for the four
    hours a night when UTC and ET disagree about the date.
    """
    return et_now().strftime('%Y-%m-%d')


def et_date_of(ts: Optional[str]) -> str:
    """The ET play date for a UTC timestamp (commence_time / kickoff_utc).

    Accepts the ISO forms the odds feeds emit, with or without a trailing
    Z. Returns '' for anything unparseable rather than guessing a date —
    a wrong date is worse than a missing one, because a missing one
    announces itself downstream.

    ══ 2026-11-02 · THE -4 CONSTANT WAS WRONG FOR ONE SPORT ═══════════════
    The module docstring argued the -4 constant was safe for a GAME time
    because "a one-hour error cannot move a game across midnight unless it
    kicks between 11pm and midnight ET — which no slate does."

    NCAAB does. Measured across every context table, 1,688 games
    (_et_offset_impact.py):

        nfl_game_context      316 games   0 dates change
        ncaaf_game_context    719 games   0
        nba_game_context      188 games   0
        nhl_game_context      327 games   0
        ncaab_game_context    138 games   2   <-- both tip 04:59 UTC

    04:59 UTC is 00:59 under -4 (so the UTC date) and 23:59 the PREVIOUS
    day under EST. Two NCAAB games were therefore being assigned tomorrow's
    date. Small, but it is the same class of defect as the three in the
    module docstring, and it lands on a sport whose game_date is being
    repaired today for an unrelated reason.

    So this now matches et_now(): zoneinfo, with the constant kept only as
    the no-tzdata fallback. The constant stays exported because the
    game-date helpers above were measured against it.
    """
    if not ts:
        return ''
    s = str(ts).strip()
    try:
        dt = _dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        # Fall back to the plain date if that is all we were handed.
        return s[:10] if len(s) >= 10 and s[4] == '-' else ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    except Exception:
        return (dt.astimezone(_dt.timezone.utc)
                - _dt.timedelta(hours=ET_OFFSET_HOURS)).strftime('%Y-%m-%d')


def et_days_ago(n: int) -> str:
    """ET date n days back, YYYY-MM-DD."""
    return (et_now() - _dt.timedelta(days=n)).strftime('%Y-%m-%d')


def et_days_ahead(n: int) -> str:
    """ET date n days forward, YYYY-MM-DD."""
    return (et_now() + _dt.timedelta(days=n)).strftime('%Y-%m-%d')


def et_and_utc_stamps(ts: Optional[str]) -> tuple:
    """(et_iso, et_compact, utc_compact) for a UTC game timestamp.

    Returns ('', '', '') for anything unparseable, so a caller can refuse
    rather than guess — a wrong date is worse than a missing one.

    ══ 2026-11-06 · R166: THREE NCAAB WRITERS, ONE UTC DATE ══════════════
    `resolve_ncaab_results`, `ncaab_backfill_results` and `ncaab_odds_pull`
    each derived a game's date with the identical two lines:

        dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
        game_date = dt.date().isoformat()
        game_id = f'ncaab_{dt.strftime("%Y%m%d")}_{away}_{home}'

    No ET conversion anywhere. A 7pm ET tip is 00:00 UTC the NEXT day, and
    for NCAAB that is most of the slate, so the game was filed a day late
    in BOTH fields. Measured on live rows:

        date        context games   results rows
        2026-11-02      119              30
        2026-11-03       19             144

    11-02's ~89 evening games are sitting under 11-03. `grade_public_receipts`
    bridges a receipt to its result on `_match_key(sport, game_date,
    away_team, home_team)` — the date is IN the join key — so a receipt on a
    11-02 game can never find its result. That is the whole of the NCAAB
    grading debt.

    Returns the UTC stamp too, because the legacy rows are keyed by it:
    changing the derivation without also LOOKING UP the old id makes the
    upsert INSERT a second row for the same game instead of patching the
    first ([[feedback_a_migration_can_be_live_then_reverted]] — judge on the
    newest artefact, and leave no duplicate behind to judge).

    Three scrapers with three copies of one derivation is the same shape as
    [[feedback_each_scraper_invented_its_own_season]]; it lives here now so
    there is one of it.
    """
    if not ts:
        return '', '', ''
    s = str(ts).strip()
    try:
        dt = _dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return '', '', ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    et_iso = et_date_of(s)
    if not et_iso:
        return '', '', ''
    return (et_iso, et_iso.replace('-', ''),
            dt.astimezone(_dt.timezone.utc).strftime('%Y%m%d'))
