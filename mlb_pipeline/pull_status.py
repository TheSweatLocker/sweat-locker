#!/usr/bin/env python3
"""Decide whether a finished external pull is honestly a success.

== WHY THIS EXISTS (2026-11-17) ===========================================
Every row in `external_pull_log` says `status='success'`. All 586 of them
since 11-10. The column carries no information at all, because status was
derived from the HTTP code (or passed in as a literal), and an HTTP 200
is a TRANSPORT fact, not an EXTRACTION fact.

What that cost: oddscrowd's last pick is 2026-11-10T19:45Z. Since then it
has run 66 times and pulled 0 picks over 0 games on 61 of them -- and
logged `success` all 66. The biggest money-flow source in the book went
dark for a week with a green light on it. The ZERO-EXTRACTION note added
on 11-16 made 7 of those 66 visible; the other 59 never reached that
branch, because it only fires when the caller computed a slate, and
`http_status` is NULL on 37 rows.

THE SIGNAL THAT IS PATH-INDEPENDENT IS `picks == 0`.

But 0 picks is not always a defect: a genuinely empty slate pulls nothing,
and an offseason sport pulls nothing for months. The distinction that
actually matters is DARK vs DORMANT:

    dormant  this source has produced nothing for a long time
             (offseason, retired, a deliberate stub) -> stay quiet
    dark     this source was producing picks days ago and is
             producing none now                      -> say so

So a 0-pick run is `failed` only when the same source wrote picks inside
the lookback window. That is self-tuning: it needs no per-source table, it
cannot fire on a stub, and it fires on the FIRST dark run rather than the
seventh ([[feedback_coverage_pct_hides_dead_code]],
[[feedback_a_lens_can_be_dark_behind_a_404]],
[[feedback_zero_rows_is_a_wiring_error]]).

There are FIVE copies of `complete_pull_log` in this directory
(externals_pro_core plus pull_externals_{mlb,ncaab,ncaaf,nfl}), so the
decision lives here and each one calls it -- fixing one writer and leaving
four is how a class survives a fix
([[feedback_fix_every_writer_to_the_table]]).
"""
from __future__ import annotations
import datetime as dt
import os

import requests

_SB = os.environ.get('SUPABASE_URL')
_KEY = (os.environ.get('SUPABASE_KEY')
        or os.environ.get('SUPABASE_SERVICE_ROLE_KEY'))

#: How far back a source must have produced picks for a 0-pick run to count
#: as DARK rather than DORMANT.
#:
#: THIRTY days, not seven. My first cut used seven and it would have read
#: oddscrowd -- the source this whole module exists for -- as DORMANT,
#: because by the time the detector was written the feed had been dead for
#: exactly seven days. A window that forgets the outage it is hunting is
#: not a detector. Thirty days keeps a source that was working this month
#: under suspicion, and a source that has produced nothing for a month is
#: genuinely retired rather than broken.
DARK_LOOKBACK_DAYS = 30

#: A 0-pick run is only a defect if there was something to pull. Checked
#: against the sport's own context table rather than a season calendar,
#: because a hardcoded "MLB ends in October" is the thing that rots
#: ([[feedback_verify_freshness_by_recency_not_date]]).
_CTX = {'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
        'NBA': 'nba_game_context', 'NHL': 'nhl_game_context',
        'NCAAF': 'ncaaf_game_context', 'NCAAB': 'ncaab_game_context'}

#: Cache per process: a pull run completes many sources, and the answer for
#: a given source cannot change inside one run.
_ALIVE: dict = {}
_HAS_SLATE: dict = {}


def _recently_alive(source: str) -> bool:
    """True when this source wrote at least one pick in the lookback."""
    if source in _ALIVE:
        return _ALIVE[source]
    alive = False
    try:
        since = (dt.datetime.now(dt.timezone.utc)
                 - dt.timedelta(days=DARK_LOOKBACK_DAYS)).isoformat()
        r = requests.get(f'{_SB}/rest/v1/external_picks',
                         headers={'apikey': _KEY,
                                  'Authorization': f'Bearer {_KEY}'},
                         params={'select': 'id', 'source': f'eq.{source}',
                                 'pulled_at': f'gte.{since}', 'limit': '1'},
                         timeout=15)
        alive = bool(r.status_code == 200 and (r.json() or []))
    except Exception:
        # Unknown is NOT dark. A read failure must never invent a failure
        # ([[feedback_zero_rows_is_a_wiring_error]]).
        alive = True
    _ALIVE[source] = alive
    return alive


def _has_slate(sport: str) -> bool:
    """True when this sport has games on the board today or yesterday.

    Two days wide because a pull can straddle midnight UTC and because an
    evening slate belongs to the ET date, not the runner's
    ([[feedback_a_window_from_runner_clock_is_a_day_ahead]]).
    """
    tbl = _CTX.get(str(sport or '').upper())
    if not tbl:
        return True            # unknown sport: do not invent a verdict
    if tbl in _HAS_SLATE:
        return _HAS_SLATE[tbl]
    ok = True
    try:
        lo = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        r = requests.get(f'{_SB}/rest/v1/{tbl}',
                         headers={'apikey': _KEY,
                                  'Authorization': f'Bearer {_KEY}'},
                         params={'select': 'game_id',
                                 'game_date': f'gte.{lo}', 'limit': '1'},
                         timeout=15)
        ok = bool(r.status_code == 200 and (r.json() or []))
    except Exception:
        ok = True
    _HAS_SLATE[tbl] = ok
    return ok


def _row_of(pull_id) -> dict:
    try:
        r = requests.get(f'{_SB}/rest/v1/external_pull_log',
                         headers={'apikey': _KEY,
                                  'Authorization': f'Bearer {_KEY}'},
                         params={'select': 'source,sport',
                                 'pull_id': f'eq.{pull_id}', 'limit': '1'},
                         timeout=15)
        j = r.json() if r.status_code == 200 else []
        return (j[0] if j else {})
    except Exception:
        return {}


def effective_status(pull_id, status: str, picks: int,
                     notes: str | None = None) -> tuple:
    """Return (status, notes) with a dark pull downgraded to 'failed'.

    `status` is passed through untouched for anything that already failed,
    and for any run that produced picks. Only the
    "fetched fine, extracted nothing, was alive yesterday" case changes,
    and the reason is appended to `notes` so the row says why.

    The CHECK constraint on the column permits success/failed/running only,
    so 'failed' is the contract's word for "this did not do its job" -- a
    softer word would be rejected with 23514 and the row would stay
    'running' forever, which is what happened before 11-16.
    """
    if str(status) != 'success' or (picks or 0) > 0 or not pull_id:
        return status, notes
    if not (_SB and _KEY):
        return status, notes
    row = _row_of(pull_id)
    src, sport = row.get('source'), row.get('sport')
    if not src or not _recently_alive(src):
        return status, notes
    if not _has_slate(sport):
        return status, notes
    why = (f'DARK: 0 picks parsed for {sport} while games are on the board, '
           f'and this source wrote picks within the last '
           f'{DARK_LOOKBACK_DAYS} days - it was working and is not now, so '
           f'this is not an empty slate')
    return 'failed', (f'{notes} | {why}' if notes else why)
