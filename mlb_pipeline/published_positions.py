#!/usr/bin/env python3
"""What side are we ALREADY on, per game, across every play surface.

== WHY (2026-11-16) =======================================================
ANDY: "for naythign that we just dfound it needs to be fixed so it stops
fucking ahppening, same shit conotinue to ahpen day after day this is
unsat".

`consistency_watchdog.cross_surface_opposite_sides` has been the single
biggest CI failure in the repo -- 35 of 67 step failures in three days --
and it is not a false alarm. Tonight, twice:

    Golden State @ New Orleans
        potd + sweat_card   Golden State Warriors ATS -3.5
        ledger leg          Golden State Warriors ML
        THE FADE            New Orleans Pelicans +3        <- opposite
    Memphis @ Washington
        dawg + sweat_card   Washington Wizards ML +146
        ledger legs (x2)    Memphis Grizzlies ML           <- opposite

Both are mutually exclusive bets a user can see at the same time, so one
surface loses no matter what happens.

THE GUARD EXISTED AND ASKED THE WRONG QUESTION. generate_ledger has had a
chalk-leg exclusion since 2026-10-03, and its own comment states the right
principle -- "PUBLISHED is the test, not merely 'the engine leaned'" -- but
it implements that as `primary_play.tier in (PRIME, STRONG, LEAN)` or
"is today's POTD". The Dawg does not go through primary_play's tier: it is
an underdog surface that publishes on its own authority, so Washington
Wizards ML was published and invisible to the test. The Fade has no
exclusion of any kind.

So this module answers the question the guard meant to ask, from the one
place that records what we actually published: live `public_receipts` rows
on play surfaces. A game read is NOT a position -- it is coverage, an
opinion on every game, most of it never staked -- which is the same
distinction the watchdog itself draws.

COMPARISON IS TEAM-LEVEL, AND THAT IS DELIBERATE. The 10-03 rule compared
only ML-vs-ML. That is narrower than the detector, which flags ANY opposite
side, so an ML-vs-spread collision would keep failing CI forever. Totals
need no special case: a total has no team, so it can never collide with a
side.

Read-only. Returns an empty mapping on any failure and says so, so a
lookup problem can never block a build -- the same fail-open posture the
exclusion it replaces already took.
"""
from __future__ import annotations

import os
import re
import sys

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except Exception:                                              # noqa: BLE001
    pass

SB = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY') or '')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: Surfaces that STAKE a play. `game_read` and `badge` are deliberately
#: absent: a read is an opinion on every game, and a surface taking the
#: other side of one is a decision, not a defect. That is the same line
#: consistency_watchdog draws, and the trap it was added for -- a detector
#: that called 59 deliberate fades CRITICAL in 8 days.
PLAY_SURFACES = ('sweat_card', 'sharp_card', 'potd', 'dotd', 'dawg', 'fade',
                 'ledger', 'daily_degen', 'ladder')

#: Market words stripped off the end of a label to leave the team.
_MKT_SUFFIX = re.compile(
    r'\s*\b(?:ml|moneyline|ats|spread|rl|run\s*line|pl|puck\s*line|'
    r'puckline)\b\s*$', re.I)
#: A trailing parenthetical ("(Jerry 55/100)", "(NHL LEAN 56/100)").
_PAREN_TAIL = re.compile(r'\s*\([^()]*\)\s*$')
#: A trailing signed number ("Tulsa -5.5", "Brooklyn Nets +4.5").
_NUM_TAIL = re.compile(r'\s*[-+]?\d+(?:\.\d+)?\s*$')
#: A label that is a TOTAL carries no team at all.
_IS_TOTAL = re.compile(r'^\s*(?:over|under|o/u)\b', re.I)


def team_of(label) -> str | None:
    """The team a pick label names, or None when it names no team.

    == 2026-11-16 . THE WATCHDOG'S OWN PARSER COULD NOT SEE THE POTD ====
    consistency_watchdog._team strips a trailing ' ML'/' ATS'/' RL'/' PL'
    or a trailing number. A POTD label ends in a parenthetical --
    "Golden State Warriors ATS -3.5 (Jerry 55/100)" -- so it stripped
    nothing, returned the whole string, matched neither team, and THE POTD
    WAS INVISIBLE to the both-sides check. That is why tonight's alert
    named ledger-vs-fade and not the far worse potd-vs-fade.
    Strip in order: parenthetical, then number, then market word, then any
    second parenthetical. [[feedback_parse_a_pick_by_shape_not_vocabulary]]
    """
    t = str(label or '').strip()
    if not t or _IS_TOTAL.search(t):
        return None
    for _ in range(3):
        before = t
        t = _PAREN_TAIL.sub('', t)
        t = _NUM_TAIL.sub('', t)
        t = _MKT_SUFFIX.sub('', t)
        t = t.strip()
        if t == before:
            break
    return t or None


def _norm(v) -> str:
    return re.sub(r'[^a-z0-9]', '', str(v or '').lower())


def published_positions(game_date: str, sport: str | None = None) -> dict:
    """{(sport, norm(away), norm(home)): {norm(team): [(surface, label)]}}

    Only LIVE (ungraded) receipts on PLAY_SURFACES. Parlay legs count as
    published sides -- half of the 2026-10-30 contradiction lived in
    `audit.legs` and was invisible until those were flattened.
    """
    if not (SB and KEY):
        print('  ! published_positions: no SUPABASE env - guard inert')
        return {}
    rows, off = [], 0
    while True:
        p = {'select': 'sport,surface,matchup,pick_label,pick_side,market,'
                       'audit',
             'game_date': f'eq.{game_date}',
             'surface': 'in.(%s)' % ','.join(PLAY_SURFACES),
             'result': 'is.null', 'order': 'id', 'offset': str(off),
             'limit': '1000'}
        try:
            r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                             params=p, timeout=30)
        except Exception as e:                                 # noqa: BLE001
            print(f'  ! published_positions read threw '
                  f'({type(e).__name__}) - guard inert this run')
            return {}
        if r.status_code != 200:
            print(f'  ! published_positions read failed {r.status_code} '
                  f'- guard inert this run')
            return {}
        j = r.json() or []
        rows += j
        if len(j) < 1000:
            break
        off += 1000
        if off > 20000:
            break

    out: dict = {}

    def _add(sp, matchup, surface, label):
        tm = team_of(label)
        if not tm or not matchup or ' @ ' not in str(matchup):
            return
        aw, hm = [x.strip() for x in str(matchup).split(' @ ', 1)]
        key = (str(sp or '').upper(), _norm(aw), _norm(hm))
        nt = _norm(tm)
        # Only a team that is actually IN this game. A label we cannot
        # attribute is dropped rather than guessed
        # ([[feedback_a_name_check_needs_the_right_row]]).
        if nt not in (_norm(aw), _norm(hm)):
            return
        out.setdefault(key, {}).setdefault(nt, []).append(
            (str(surface), str(label)))

    for x in rows:
        aud = x.get('audit') if isinstance(x.get('audit'), dict) else {}
        legs = aud.get('legs')
        if isinstance(legs, list) and legs:
            for lg in legs:
                if isinstance(lg, dict) and lg.get('pick'):
                    _add(lg.get('sport') or x.get('sport'),
                         lg.get('matchup') or x.get('matchup'),
                         f"{x.get('surface')}/leg", lg.get('pick'))
            continue
        _add(x.get('sport'), x.get('matchup'), x.get('surface'),
             x.get('pick_label'))

    if sport:
        sp = str(sport).upper()
        out = {k: v for k, v in out.items() if k[0] == sp}
    return out


def opposing(positions: dict, sport: str, away, home, team) -> list:
    """[(surface, label)] already published on the OTHER side of this game.

    `team` is the side about to be staked. Empty list means no conflict.
    """
    key = (str(sport or '').upper(), _norm(away), _norm(home))
    held = positions.get(key) or {}
    mine = _norm(team)
    out = []
    for t, srcs in held.items():
        if t and t != mine:
            out += srcs
    return out


def _self_test() -> int:
    bad = []

    def ck(name, got, want):
        if got != want:
            bad.append(f'{name}: got {got!r} want {want!r}')

    # The parser, including the shape that defeated the watchdog.
    ck('potd label', team_of('Golden State Warriors ATS -3.5 '
                             '(Jerry 55/100)'), 'Golden State Warriors')
    ck('bare potd label', team_of('Golden State Warriors ATS '
                                  '(Jerry 55/100)'),
       'Golden State Warriors')
    ck('plain ml', team_of('Washington Wizards ML'), 'Washington Wizards')
    ck('signed spread', team_of('Tulsa -5.5'), 'Tulsa')
    ck('puckline', team_of('Winnipeg Jets PL'), 'Winnipeg Jets')
    ck('lean suffix', team_of('St. Thomas ML lean'), 'St. Thomas ML lean')
    ck('abbrev + line + paren', team_of('BUF ATS -8 (Jerry 65/100)'), 'BUF')
    ck('nhl rl', team_of('Buffalo Sabres -1.5 (NHL LEAN 56/100)'),
       'Buffalo Sabres')
    # A total names no team, so it can never collide with a side.
    ck('total over', team_of('Over 6.5'), None)
    ck('total under', team_of('Under 148.5'), None)
    # The substring trap: a Wildcats/Bearcats ML must keep its full name.
    ck('wildcats', team_of('Arizona Wildcats ML'), 'Arizona Wildcats')
    ck('bearcats', team_of('Cincinnati Bearcats ML'), 'Cincinnati Bearcats')
    ck('trail blazers', team_of('Portland Trail Blazers ML'),
       'Portland Trail Blazers')

    # opposing()
    pos = {('NBA', _norm('Memphis Grizzlies'), _norm('Washington Wizards')): {
        _norm('Washington Wizards'): [('dawg', 'Washington Wizards ML')]}}
    ck('opposite side is flagged',
       opposing(pos, 'NBA', 'Memphis Grizzlies', 'Washington Wizards',
                'Memphis Grizzlies'),
       [('dawg', 'Washington Wizards ML')])
    ck('same side is not flagged',
       opposing(pos, 'NBA', 'Memphis Grizzlies', 'Washington Wizards',
                'Washington Wizards'), [])
    ck('other game is not flagged',
       opposing(pos, 'NBA', 'Boston Celtics', 'Miami Heat',
                'Boston Celtics'), [])
    ck('other sport is not flagged',
       opposing(pos, 'NHL', 'Memphis Grizzlies', 'Washington Wizards',
                'Memphis Grizzlies'), [])

    if bad:
        print(f'published_positions self-test FAILED {len(bad)} case(s):')
        for b in bad:
            print(f'  - {b}')
        return 1
    print('published_positions self-test: all cases pass')
    return 0


if __name__ == '__main__':
    sys.exit(_self_test())
