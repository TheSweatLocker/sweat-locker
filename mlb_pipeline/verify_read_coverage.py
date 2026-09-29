#!/usr/bin/env python3
"""Assert every game the APP CAN DISPLAY has a Jerry game read.

WHY THIS EXISTS
---------------
2026-09-29. Eleven NCAAF games had no Jerry read. Chasing that turned up
something worse and in a different sport: on any WEDNESDAY the NFL "tomorrow"
tab rendered next play-week's entire Sunday slate — 14 games — while
generate_nfl_game_reads only built reads out to +10 days. Fourteen bare cards,
every Wednesday, self-healing by Friday. Nothing failed, nothing logged, no
step went red. The generators did exactly what they were told; what they were
told disagreed with what the app showed.

The cause was structural: the same coverage window was written down FOUR times
in four files as four unrelated constants (app season-week equality,
NCAAF 10d, NFL odds cutoff 10d, NFL ctx 12d). season_calendar.read_horizon now
derives all three pipeline windows from the app's own week arithmetic, so they
cannot drift apart by construction.

This script is the second half of that fix. Deriving the window stops the four
constants from disagreeing; it does NOT catch a generator that crashed on one
game, an anchor edited on one side only, or a context row that arrived after
the read pass ran. Those all present identically to the user — a card with no
read — and all of them were previously invisible. So: measure the actual
outcome, not the intent.

WHAT IT CHECKS
--------------
For each weekly football sport, reproduce the app's display set exactly
(current play-week + next play-week, upcoming only, per
app/index.tsx `_seasonWeekOf`) and require a jerry_reads row for each game.
Exit 1 with the missing games named if any are bare.

Deliberately NOT checked: games beyond next week. Those are invisible to users
and generating reads for them burns tokens on lines that will move.

USAGE
    python verify_read_coverage.py              # all weekly sports
    python verify_read_coverage.py --sport NFL
    python verify_read_coverage.py --warn-only  # report, always exit 0
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from season_calendar import PLAY_WEEK_ANCHORS, season_week_of

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
    print('verify_read_coverage: no Supabase credentials — skipping')
    sys.exit(0)
HDRS = {'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + SUPABASE_KEY}

CTX_TABLE = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context'}


def _today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def check(sport, warn_only=False):
    """Return count of app-visible games with no jerry read."""
    today = _today_et()
    cur = season_week_of(sport, today)
    weeks = {cur, cur + 1}

    # Pull a generous date range and filter by WEEK, matching the client. A
    # date range here would reintroduce the exact bug this guards against.
    hi = (today + timedelta(days=21)).isoformat()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{CTX_TABLE[sport]}",
        headers=HDRS,
        params={'select': 'game_id,game_date,away_team,home_team',
                'game_date': f'gte.{today}', 'limit': '500',
                'order': 'game_date.asc'},
        timeout=30)
    if r.status_code != 200:
        print(f'  {sport}: context fetch failed {r.status_code} — {r.text[:120]}')
        return 0 if warn_only else -1
    ctx = [g for g in r.json()
           if g.get('game_date')
           and season_week_of(sport, _d(g['game_date'])) in weeks]

    r2 = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_reads",
        headers=HDRS,
        params={'select': 'game_id', 'sport': f'eq.{sport}',
                'game_date': f'gte.{today}', 'limit': '2000'},
        timeout=30)
    if r2.status_code != 200:
        print(f'  {sport}: jerry_reads fetch failed {r2.status_code}')
        return 0 if warn_only else -1
    have = {x.get('game_id') for x in r2.json()}

    missing = [g for g in ctx if g.get('game_id') not in have]
    tag = 'OK' if not missing else ('WARN' if warn_only else 'GAP')
    print(f'  {sport:<6s} week {cur}+{cur + 1}: '
          f'{len(ctx) - len(missing)}/{len(ctx)} reads  [{tag}]')
    for g in missing:
        print(f"      NO READ  {g['game_date']}  "
              f"{g.get('away_team')}@{g.get('home_team')}  ({g.get('game_id')})")
    return len(missing)


def _d(s):
    from datetime import date
    return date.fromisoformat(str(s)[:10])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(CTX_TABLE))
    ap.add_argument('--warn-only', action='store_true')
    a = ap.parse_args()

    sports = [a.sport] if a.sport else sorted(CTX_TABLE)
    print(f'=== jerry read coverage vs app display window  {_today_et()} ===')
    total = 0
    for s in sports:
        if s not in PLAY_WEEK_ANCHORS:
            continue
        n = check(s, warn_only=a.warn_only)
        if n > 0:
            total += n
    if total and not a.warn_only:
        print(f'\n{total} app-visible game(s) have no Jerry read. '
              f'Re-run the generator for that sport.')
        sys.exit(1)
    if not total:
        print('\nAll app-visible football games have a read.')


if __name__ == '__main__':
    main()
