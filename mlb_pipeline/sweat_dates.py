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

ET is UTC-4 here year-round on purpose. DST would make it -5 in winter,
but the only thing these functions decide is which calendar day a game
belongs to, and a one-hour error cannot move a game across midnight
unless it kicks between 11pm and midnight ET — which no slate does.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

#: Hours behind UTC. See the module docstring for why this is a constant.
ET_OFFSET_HOURS = 4


def et_now() -> _dt.datetime:
    """Current wall-clock time in ET, naive."""
    return _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(
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
    return (dt.astimezone(_dt.timezone.utc)
            - _dt.timedelta(hours=ET_OFFSET_HOURS)).strftime('%Y-%m-%d')


def et_days_ago(n: int) -> str:
    """ET date n days back, YYYY-MM-DD."""
    return (et_now() - _dt.timedelta(days=n)).strftime('%Y-%m-%d')


def et_days_ahead(n: int) -> str:
    """ET date n days forward, YYYY-MM-DD."""
    return (et_now() + _dt.timedelta(days=n)).strftime('%Y-%m-%d')
