"""Should this sport's workflow do any work right now?

== WHY ====================================================================
Andy, 2026-11-03: "def think we need to sideline mlb run while in offseason
right, more efficient that way."

Measured the same day: **231.9 scheduled workflow runs a day across 80
crons, and ~74 of them are MLB-only** -- 32% of the whole schedule on a
season whose last game was **2026-10-28**. None of the 11 MLB workflows
carried any season gate at all:

    mlb_imminent_refresh   22/day      mlb_refresh_missing     5/day
    mlb_line_poller        18/day      mlb_pipeline            4/day
    mlb_oddscrowd_refresh  12/day      mlb_grade_overnight     3/day
    mlb_watchdogs           6/day      mlb_starter_retry       2/day
                                       mlb_close_line_capture  1/day
                                       mlb_prop_calibration    1/day
                                       mlb_refit_weekly        1/week

That congestion is not free: it is the measured cause of the sport
pipelines landing 7-10 hours late, which is why the Sweat Card composes on
yesterday's non-MLB data every day
(project_card_builds_before_every_other_sport_1102).

== WHY NOT JUST READ sport_registry.state =================================
Because it is a DATE GUESS, not an observation. Today it says MLB
state='in_season' with season_end='2026-11-05' -- a plausible World Series
end date -- while the last MLB game actually played was 2026-10-28 and
mlb_game_context holds no future rows at all. A gate on `state` alone would
have kept all 74 runs alive for another two days, and would do the same
every year the postseason ends early.

So `--require-games` asks the question the workflow actually cares about:
is there a game to poll? That is self-maintaining -- it covers the next
offseason, and the All-Star break, with no date to update.

It is deliberately NOT used on mlb_pipeline itself. That workflow WRITES
mlb_game_context, so gating it on that table being non-empty is circular:
one empty day and it could never populate the table again. It gates on
`state` only, which a human or auto_flip_sport_state maintains.

== FAILS OPEN =============================================================
Any error -- unreachable DB, missing row, unknown state -- prints a warning
and returns run=true. A gate that silently stops an in-season pipeline is
far worse than one that lets an offseason run through: the whole family of
"a GREEN step wrote NOTHING" bugs started this way
(feedback_green_step_that_writes_nothing).

    python sport_gate.py --sport MLB                       # state only
    python sport_gate.py --sport MLB --require-games       # + real games
    python sport_gate.py --sport MLB --grace-days 10       # graders
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

#: Sports whose slate lives in <prefix>_game_context.
_CTX = {
    'MLB': 'mlb_game_context',
    'NBA': 'nba_game_context',
    'NHL': 'nhl_game_context',
    'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NCAAB': 'ncaab_game_context',
}

_RUN_STATES = ('in_season', 'preseason')


def _load_env() -> None:
    env = Path(__file__).with_name('.env')
    if env.exists():
        for line in env.read_text(encoding='utf-8', errors='replace').split('\n'):
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


def _get(table: str, params: dict) -> list:
    base = os.environ['SUPABASE_URL'].rstrip('/')
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ['SUPABASE_KEY'])
    r = requests.get(f'{base}/rest/v1/{table}',
                     headers={'apikey': key, 'Authorization': 'Bearer ' + key},
                     params=params, timeout=45)
    r.raise_for_status()
    return r.json() or []


def _et_today() -> date:
    from sweat_dates import et_today
    return date.fromisoformat(et_today())


def decide(sport: str, grace_days: int, require_games: bool,
           horizon_days: int) -> tuple[bool, str]:
    """(run, reason). Raises on anything it cannot determine -- main() turns
    that into run=true."""
    today = _et_today()
    rows = _get('sport_registry', {'select': 'sport,state,season_start,'
                                             'season_end',
                                   'sport': f'eq.{sport}'})
    if not rows:
        raise RuntimeError(f'no sport_registry row for {sport!r}')
    row = rows[0]
    state = str(row.get('state') or '').strip().lower()
    season_end = row.get('season_end')

    if state not in _RUN_STATES:
        # off_season (or anything unexpected that is not a run state).
        if not season_end:
            return False, f'state={state!r}, no season_end to grace from'
        try:
            end = date.fromisoformat(str(season_end))
        except ValueError:
            raise RuntimeError(f'unparseable season_end {season_end!r}')
        if today <= end + timedelta(days=grace_days):
            return True, (f'state={state!r} but within {grace_days}d grace of '
                          f'season_end {end}')
        return False, (f'state={state!r}, season_end {end}, grace '
                       f'{grace_days}d expired')

    if not require_games:
        return True, f'state={state!r}'

    ctx = _CTX.get(sport)
    if not ctx:
        raise RuntimeError(f'no game_context table known for {sport!r}')
    horizon = today + timedelta(days=horizon_days)
    games = _get(ctx, {'select': 'game_date',
                       'game_date': f'gte.{today.isoformat()}',
                       'limit': 200})
    upcoming = [g for g in games
                if str(g.get('game_date') or '') <= horizon.isoformat()]
    if upcoming:
        return True, (f'state={state!r}, {len(upcoming)} game(s) in '
                      f'{today}..{horizon}')
    # state says in season but there is nothing on the board. That is the
    # MLB-after-the-World-Series case, and also any genuine dark day.
    future = [g for g in games if str(g.get('game_date') or '')]
    nxt = min((str(g['game_date']) for g in future), default=None)
    return False, (f'state={state!r} but 0 games in {today}..{horizon}'
                   + (f' (next on board: {nxt})' if nxt else ', none on board'))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True)
    ap.add_argument('--grace-days', type=int, default=0,
                    help='keep running this many days past season_end '
                         '(graders need a tail; live-market jobs do not)')
    ap.add_argument('--require-games', action='store_true',
                    help='also require a game on the board. NEVER use this '
                         'on the workflow that writes <sport>_game_context.')
    ap.add_argument('--horizon-days', type=int, default=1)
    args = ap.parse_args()

    sport = args.sport.upper()
    try:
        _load_env()
        run, reason = decide(sport, args.grace_days, args.require_games,
                             args.horizon_days)
    except Exception as e:                                   # noqa: BLE001
        run = True
        reason = (f'GATE ERROR ({type(e).__name__}: {e}) -- failing OPEN. A '
                  f'gate must never be the reason an in-season pipeline goes '
                  f'dark.')

    print(f'[sport_gate] {sport}: run={str(run).lower()} -- {reason}',
          flush=True)
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as fh:
            fh.write(f'run={str(run).lower()}\n')
            # One line, always. A GITHUB_OUTPUT value containing a
            # newline corrupts every later key in the file, and `reason`
            # is free text that an exception message feeds into.
            fh.write('reason=' + ' '.join(str(reason).split()) + chr(10))
    return 0


if __name__ == '__main__':
    sys.exit(main())
