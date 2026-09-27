"""Do not publish picks for a sport that has not started yet.

Andy 2026-09-26, asking whether we are ready for the incoming sports.
Measured that day, one week before NBA's first listed game:

    NBA game context      81 games, ALL dated 2026-10-03 .. 2026-10-21
    NBA regular season    starts 2026-10-21 (sport_registry.season_start)
    NBA picks generated   76
       68 of 69 side picks on the HOME team
       64 of 76 at tier STRONG
       58 of 76 with NO market line at all
       reasoning string   "Model conviction on Nuggets · 59%"
    NBA supporting data   0 current team stats, 0 SOS/SOR, 0 Jerry reads,
                          0 externals, 0 signal records

Every one of those games is PRESEASON. Starters play eighteen minutes,
rotations are experimental, and the model has no current-season data to
work from — which is exactly why nba_pipeline.yml already no-ops until
the opener. The pick generator was the one component that did not know.

THE REGISTRY ALREADY KNEW. sport_registry carries state='preseason',
season_start and return_date for every sport, and auto_flip_sport_state
keeps them current. Nothing in the pick path ever read it. So this is
not a new source of truth competing with the old ones — it is the
existing declaration finally being enforced, which is the opposite of
the "another layer fights legacy" problem.

Universal on purpose (feedback_universal_vs_sport_specific): NCAAB opens
2026-11-03 with nothing built, and NHL's registry still reads preseason
while games are already being graded. One gate covers all of them.

    from sport_season_gate import season_gate
    ok, why = season_gate('NBA', '2026-10-05')
    if not ok:
        ...skip publishing this pick...
"""
from __future__ import annotations

import os
from datetime import date, datetime

import requests

_CACHE: dict = {}

# States in which a sport must not publish picks. 'in_season' and
# anything unrecognised are allowed through — a sport we have not
# classified should not be silently muted.
_BLOCKED_STATES = {'preseason', 'offseason'}


def _env():
    sb = os.environ.get('SUPABASE_URL')
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ.get('SUPABASE_KEY'))
    return sb, ({'apikey': key, 'Authorization': f'Bearer {key}'} if key else None)


def _registry() -> dict:
    """{SPORT: row} from sport_registry, cached for the process."""
    if 'v' in _CACHE:
        return _CACHE['v']
    out: dict = {}
    sb, h = _env()
    if sb and h:
        try:
            r = requests.get(f'{sb}/rest/v1/sport_registry', headers=h,
                             params={'select': '*'}, timeout=20)
            if r.status_code == 200:
                for row in (r.json() or []):
                    if row.get('sport'):
                        out[str(row['sport']).upper()] = row
        except Exception:
            pass          # unreachable registry must not block a pipeline
    _CACHE['v'] = out
    return out


def _as_date(v):
    if isinstance(v, date):
        return v
    if not v:
        return None
    try:
        return datetime.strptime(str(v)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def season_gate(sport: str, game_date=None) -> tuple[bool, str]:
    """-> (publishable, reason).

    Blocks when the sport's registry state says it has not started, or
    when the game falls before its declared season_start. Fails OPEN —
    an unknown sport, an unreachable registry or a missing season_start
    all return True, because silently muting a live sport is a worse
    failure than publishing an early one.
    """
    key = str(sport or '').upper()
    row = _registry().get(key)
    if not row:
        return True, ''

    gd = _as_date(game_date)
    start = _as_date(row.get('season_start'))

    # A dated game before the declared opener is preseason regardless of
    # what the state flag currently says — the flag flips on a cron and
    # can lag, the date cannot.
    if gd and start and gd < start:
        return False, (f'{key} game on {gd} is before the declared season '
                       f'start {start} — preseason, not published')

    state = str(row.get('state') or '').lower()
    if state in _BLOCKED_STATES:
        # With no game date to check we can only trust the flag.
        if not (gd and start):
            return False, (f'{key} registry state is "{state}" — '
                           f'not published')
        # Dated game on/after the opener: the flag is simply stale, and
        # the date already cleared it above.
    return True, ''


def assert_publishable(sport: str, game_date=None, *, verbose: bool = True) -> bool:
    ok, why = season_gate(sport, game_date)
    if not ok and verbose and why:
        print(f'  ⛔ {why}')
    return ok
