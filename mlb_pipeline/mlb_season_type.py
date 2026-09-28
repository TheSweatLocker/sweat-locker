"""What KIND of MLB game is this — regular season, Wild Card, LCS, World Series?

Andy 2026-09-28: "debrief MLB reg season, expectation for MLB playoffs, will
Jerry know when writing up."

He will not, today. mlb_game_context has 321 columns and not one of them is
season_type — NFL and NCAAF both carry it. Nothing in the MLB path mentions
postseason or playoff at all: not game_context.py, not
generate_jerry_synthesis.py, not jerry_anchor_potd.py. So Jerry writes about
a Wild Card elimination game in exactly the voice it uses for a Tuesday in
June, and every cohort, tier and projection treats it as one more regular
season game.

The slate itself arrives fine — the pipeline reads it from the Odds API,
which already prices all four Wild Card series, and there is no
regular-season filter anywhere. So the games show up unlabelled, which is
the worst of both: present, and silently mis-modelled.

WHY THIS IS A LOOKUP AND NOT A DATE RANGE. "October means playoffs" is
wrong in both directions — the regular season regularly runs into October,
and the World Series runs into November. MLB StatsAPI states the answer
directly in `gameType`, so this asks rather than infers.

WHAT IT IS NOT FOR. This does not try to make the models right for October.
Eight teams and ~32 games is not a sample anything can be fitted to, and
pretending otherwise is how the leaked prop PRIME happened. It exists so the
engine and Jerry can KNOW, and so a separate guard can be conservative where
regular-season assumptions provably do not hold: compressed rotations, all
hands bullpens, and September stats describing lineups that rested for two
weeks after clinching.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

SCHEDULE = 'https://statsapi.mlb.com/api/v1/schedule'

# MLB StatsAPI gameType codes.
GAME_TYPE = {
    'R': 'REGULAR',
    'F': 'WILDCARD',
    'D': 'DIVISION_SERIES',
    'L': 'LCS',
    'W': 'WORLD_SERIES',
    'S': 'SPRING',
    'E': 'EXHIBITION',
    'A': 'ALL_STAR',
}
POSTSEASON = {'WILDCARD', 'DIVISION_SERIES', 'LCS', 'WORLD_SERIES'}

# Human phrasing for prose, so Jerry is handed the words rather than a code.
LABEL = {
    'WILDCARD': 'Wild Card round',
    'DIVISION_SERIES': 'Division Series',
    'LCS': 'League Championship Series',
    'WORLD_SERIES': 'World Series',
    'REGULAR': 'regular season',
}

_CACHE: dict = {}


def _norm(name: str) -> str:
    return ''.join(ch for ch in str(name or '').lower() if ch.isalnum())


def schedule_for(game_date: str) -> dict:
    """-> {(away_norm, home_norm): {'type', 'label', 'is_postseason',
                                    'series_game', 'description'}}"""
    if game_date in _CACHE:
        return _CACHE[game_date]
    out: dict = {}
    try:
        r = requests.get(SCHEDULE,
                         params={'sportId': 1, 'startDate': game_date,
                                 'endDate': game_date,
                                 # every type, so a mixed day (final regular
                                 # season games + a tiebreaker) resolves
                                 'gameTypes': 'R,F,D,L,W,S,E,A'},
                         timeout=20)
        if r.status_code != 200:
            return {}
        for d in (r.json().get('dates') or []):
            for g in (d.get('games') or []):
                code = str(g.get('gameType') or '')
                t = GAME_TYPE.get(code, 'REGULAR')
                teams = g.get('teams') or {}
                away = ((teams.get('away') or {}).get('team') or {}).get('name')
                home = ((teams.get('home') or {}).get('team') or {}).get('name')
                if not away or not home:
                    continue
                out[(_norm(away), _norm(home))] = {
                    'type': t,
                    'label': LABEL.get(t, t.replace('_', ' ').title()),
                    'is_postseason': t in POSTSEASON,
                    # seriesGameNumber is present for postseason; "game 2 of
                    # a best-of-three" is a materially different spot from
                    # game 1 and Jerry should be able to say so.
                    'series_game': g.get('seriesGameNumber'),
                    'games_in_series': g.get('gamesInSeries'),
                    'description': g.get('description') or '',
                }
    except Exception:
        return {}
    _CACHE[game_date] = out
    return out


def for_game(game_date: str, away_team: str, home_team: str) -> dict:
    """Season type for one matchup. Falls back to REGULAR, never raises.

    Falling back to REGULAR rather than None is deliberate: a lookup failure
    must not make an ordinary June game look like a playoff game. The failure
    mode we want is 'treated as normal', not 'treated as special'.
    """
    unknown = {'type': 'REGULAR', 'label': LABEL['REGULAR'],
               'is_postseason': False, 'series_game': None,
               'games_in_series': None, 'description': '', 'resolved': False}
    sched = schedule_for(game_date)
    if not sched:
        return unknown
    hit = sched.get((_norm(away_team), _norm(home_team)))
    if not hit:
        # Postseason matchups are listed with placeholder names until the
        # prior round settles ("HOU/CWS @ Cleveland Guardians"). If EVERY
        # game that day is postseason, this one is too, whatever the names.
        types = {v['type'] for v in sched.values()}
        if types and types <= POSTSEASON:
            t = sorted(types)[0]
            return {'type': t, 'label': LABEL.get(t, t), 'is_postseason': True,
                    'series_game': None, 'games_in_series': None,
                    'description': '', 'resolved': False}
        return unknown
    return dict(hit, resolved=True)


def prose_note(info: dict) -> str:
    """One sentence for the Jerry facts block. Empty for regular season."""
    if not info or not info.get('is_postseason'):
        return ''
    head = f'This is a POSTSEASON game ({info["label"]})'
    sg, gis = info.get('series_game'), info.get('games_in_series')
    if sg and gis:
        head += f', game {sg} of a best-of-{gis} series'
    elif sg:
        head += f', game {sg} of the series'
    return (
        head + '. '
        'Playoff baseball breaks several regular-season assumptions and the '
        'read should reflect that: rotations compress so aces start on short '
        'rest and fourth/fifth starters do not pitch, bullpens are available '
        'every night rather than managed for a 162-game grind, and '
        'September team stats describe lineups that were resting regulars '
        'after clinching. Season-long splits and recent-form numbers are '
        'therefore weaker evidence here than usual — say so rather than '
        'citing them as if nothing changed.'
    )


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    _e = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(_e):
        for _l in open(_e, encoding='utf-8'):
            if '=' in _l and not _l.startswith('#'):
                _k, _v = _l.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())
    dates = sys.argv[1:] or ['2026-09-27', '2026-09-29', '2026-10-03']
    for d in dates:
        sched = schedule_for(d)
        print(f'=== {d}: {len(sched)} games ===')
        for (a, h), v in list(sched.items())[:6]:
            sg = f" g{v['series_game']}/{v['games_in_series']}" if v.get('series_game') else ''
            print(f"    {v['type']:16s}{sg:8s} {a} @ {h}")
        if sched:
            first = list(sched.values())[0]
            note = prose_note(first)
            if note:
                print(f'\n    Jerry note: {note[:150]}...')
        print()
