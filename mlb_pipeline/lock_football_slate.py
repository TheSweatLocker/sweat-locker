#!/usr/bin/env python3
"""Stamp pick_locked_at on the current football slate, on schedule.

WHY THIS EXISTS
---------------
Andy 2026-09-29: "if the new engine changed we are still early in week we
should lock college picks until weekend and ncaaf thursday"

Migration 20260926b made the pick lock a STAMP applied by a DB trigger on first
publish, on purpose, to avoid duplicating pick_lock.py's schedule in SQL. The
reasoning was right and the effect was wrong: a pick became permanent the
instant it was first written, on any day, and that migration's closing backfill
stamped every row that already had one. Measured 2026-09-29:

    NCAAF    24 of 67 upcoming locked, out to 10-17
    NFL     208 of 224 upcoming locked, out to 2027-01-10

The whole remaining NFL season was frozen at its September 26th state, and
pick_lock_drift recorded 192 refused changes in one day — including the
barred-total reroutes and sign-flip corrections shipped that same day.

20260929c removes the auto-stamp. This script is the replacement: the schedule
stays in exactly one place (pick_lock.lock_active), and the database keeps doing
only what a database should — enforce, not decide when.

WHAT IT DOES
------------
For each weekly football sport, stamp pick_locked_at on upcoming games that
have a pick and are not yet stamped, but ONLY when:

  * pick_lock.lock_active(sport) is True — NCAAF Thu 8am ET -> Sun,
    NFL Thu 8am ET -> Mon, per _WEEKLY_LOCK; or
  * the game kicks off within LOCK_IMMINENT_HOURS. This covers the
    Wednesday-night NFL game that a Thursday window would otherwise never
    lock, and any game rescheduled into a gap.

and ONLY for games kicking off within LOCK_SLATE_DAYS. A game three weeks out
has never been shown to anyone (season_calendar.read_horizon bounds what the app
displays), so freezing its pick protects nothing and costs everything.

A "current play-week" test was tried first and is deliberately NOT used: on
Tue 09-29 season_week_of said week 5 for NCAAF while every upcoming game was
week 6 — the week label had rolled past its own slate, so nothing matched and
nothing would have locked. It happens to realign by Thursday, but a lock that
depends on two calendars agreeing is a lock that silently does nothing the day
they do not. Days-to-kickoff needs no alignment.

Designed to run on EVERY football pipeline invocation. It is idempotent and
cheap, so no single cron firing is a point of failure — any run inside the
window locks the slate.

USAGE
    python lock_football_slate.py                  # both sports
    python lock_football_slate.py --sport NCAAF
    python lock_football_slate.py --dry-run
    python lock_football_slate.py --unlock --sport NCAAF   # deliberate release
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from pick_lock import lock_active
from season_calendar import read_horizon

LOCK_IMMINENT_HOURS = 24
# The slate being played. Thu->Wed covers a full football play-week.
LOCK_SLATE_DAYS = 7

CTX_TABLE = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context'}

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV):
        _ln = _ln.strip()
        if _ln and not _ln.startswith('#') and '=' in _ln:
            _k, _v = _ln.split('=', 1)
            os.environ.setdefault(_k, _v.strip().strip('"'))

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
                or os.environ.get('SUPABASE_KEY'))
if not SUPABASE_URL or not SUPABASE_KEY:
    print('lock_football_slate: no Supabase credentials — skipping')
    sys.exit(0)
HDRS = {'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + SUPABASE_KEY,
        'Content-Type': 'application/json'}


def _today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _iso(s):
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None


def run(sport, dry=False, unlock=False):
    tbl = CTX_TABLE[sport]
    today = _today_et()
    horizon = read_horizon(sport, on=today)
    now = datetime.now(timezone.utc)

    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/{tbl}',
        headers=HDRS,
        params={'select': 'game_id,game_date,kickoff_utc,away_team,home_team,'
                          'pick_locked_at,primary_play',
                'game_date': f'gte.{today}', 'limit': '500',
                'order': 'game_date.asc'},
        timeout=30)
    if r.status_code != 200:
        print(f'  {sport}: fetch failed {r.status_code} {r.text[:150]}')
        return 0
    rows = r.json()

    if unlock:
        targets = [g for g in rows if g.get('pick_locked_at')
                   and (_iso(g.get('kickoff_utc')) or now) > now]
        print(f'  {sport}: UNLOCK {len(targets)} upcoming game(s)')
        for g in targets:
            if dry:
                continue
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/{tbl}?game_id=eq.{g['game_id']}",
                headers=HDRS, json={'pick_locked_at': None}, timeout=30)
        return len(targets)

    window_open = lock_active(sport)

    todo, why = [], {}
    for g in rows:
        if not g.get('primary_play') or g.get('pick_locked_at'):
            continue
        ko = _iso(g.get('kickoff_utc'))
        if ko and ko <= now:
            continue                       # already started; trigger covers it
        gd = g.get('game_date')
        try:
            from datetime import date as _d
            gdate = _d.fromisoformat(str(gd)[:10])
        except Exception:
            continue
        # Only the visible slate. Anything past the app's horizon is unpublished.
        if horizon and gdate > horizon:
            continue
        imminent = bool(ko and (ko - now) <= timedelta(hours=LOCK_IMMINENT_HOURS))
        on_slate = bool(ko and (ko - now) <= timedelta(days=LOCK_SLATE_DAYS))
        if window_open and on_slate:
            todo.append(g); why[g['game_id']] = 'lock window'
        elif imminent:
            todo.append(g); why[g['game_id']] = 'kickoff <%dh' % LOCK_IMMINENT_HOURS

    print(f'  {sport}: lock_active={window_open} slate=<={LOCK_SLATE_DAYS}d '
          f'horizon<={horizon} | {len(todo)} to stamp of {len(rows)} upcoming')
    for g in todo:
        pp = g.get('primary_play') or {}
        print(f"      {'DRY ' if dry else ''}LOCK {g['game_date']} "
              f"{g['away_team']}@{g['home_team']}  "
              f"{pp.get('label')}  [{why[g['game_id']]}]")
        if dry:
            continue
        pr = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{tbl}?game_id=eq.{g['game_id']}",
            headers=HDRS,
            json={'pick_locked_at': datetime.now(timezone.utc).isoformat()},
            timeout=30)
        if pr.status_code not in (200, 204):
            print(f'        ! patch {pr.status_code} {pr.text[:120]}')
    return len(todo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(CTX_TABLE))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--unlock', action='store_true',
                    help='Deliberately release upcoming locks for the sport. '
                         'Explicit and auditable, per 20260926b.')
    a = ap.parse_args()
    sports = [a.sport] if a.sport else sorted(CTX_TABLE)
    print(f'=== lock_football_slate {_today_et()} ===')
    for s in sports:
        run(s, dry=a.dry_run, unlock=a.unlock)


if __name__ == '__main__':
    main()
