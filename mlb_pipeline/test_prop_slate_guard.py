# -*- coding: utf-8 -*-
"""Regression: a prop may only reach the card if its game is still to play.

== WHY THIS EXISTS (2026-11-26) ==========================================
Andy, looking at the Sweat Card: "Seeing some plays undgraded form eysterday
sweat card". Two of today's FOUR card items were NBA props on games that had
already finished, on Thanksgiving -- a day the NBA does not play at all.

The chain, all three links measured:

  1. nba_generate_props._et_today() returned (now_utc - 4h).date(), which is
     EDT. In EST that is a DAY AHEAD for every run between 04:00 and 04:59
     UTC -- 127 days a year, already documented in
     grade_public_receipts._et_shift_dst and sweat_dates. The 22:41 UTC cron
     landed at 04:21 UTC (GitHub cron lateness) and wrote 210 prop rows
     under game_date 2026-11-26 for Kings @ Warriors and Spurs @ Blazers,
     both IN PROGRESS at that moment and final by morning (110-129 and
     111-116, both on 11-25).
  2. Those rows carry an Odds-API event id that appears nowhere in
     nba_game_context, so the card kickoff filter had no start time to judge
     and passed them (feedback_a_null_never_reaches_a_gate).
  3. The card was short, so the CARD_FLOOR ladder shipped 2026-11-25 pulled
     two of them onto the card to reach its weak floor of 3.

Fixing only the writer would leave links 2 and 3 live for the next writer
that gets a date wrong, so the readers now resolve the slate from the
context table instead of trusting one column.

OFFLINE: stubs sb_get. Writes nothing, reads nothing.

    python test_prop_slate_guard.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fails = []


def chk(name, got, want):
    ok = got == want
    print(('  PASS ' if ok else '  FAIL ') + name + '  got=%r want=%r'
          % (got, want))
    if not ok:
        fails.append(name)


# ── stub the env the module needs before import ───────────────────────────
os.environ.setdefault('SUPABASE_URL', 'http://localhost')
os.environ.setdefault('SUPABASE_KEY', 'test')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY', 'test')

import generate_sweat_card as G    # noqa: E402

AHEAD = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
PAST = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()


def run_guard(props, ctx_rows, sport='NBA', date='2026-11-26'):
    real = G.sb_get
    G.sb_get = lambda tbl, params=None, **kw: (
        None if ctx_rows is None else list(ctx_rows))
    G._SLATE_CACHE.clear()
    try:
        return G.prop_rows_on_slate(props, sport, date, 'test: ')
    finally:
        G.sb_get = real
        G._SLATE_CACHE.clear()


print('== THE 2026-11-26 CASE: a prop on a game the sport does not play ==')
phantom = [
    {'matchup': 'Sacramento Kings @ Golden State Warriors',
     'game_id': 'e6df92326adb480e427bb7c10f445c60',
     'player_name': "De'Anthony Melton", 'prop_type': 'pts_under'},
    {'matchup': 'San Antonio Spurs @ Portland Trail Blazers',
     'game_id': '9aa9f220709533d70d11f85fae886771',
     'player_name': 'Deni Avdija', 'prop_type': 'pra_under'},
]
chk('no NBA games tonight -> every prop refused',
    len(run_guard(phantom, [])), 0)

print('== a prop on a game that IS on tonight slate survives ==')
live_ctx = [{'away_team': 'Sacramento Kings',
             'home_team': 'Golden State Warriors',
             'game_id': '401909324', 'commence_time': AHEAD}]
chk('matched on the matchup, with a different game_id space',
    len(run_guard(phantom, live_ctx)), 1)
chk('and the one kept is the right one',
    run_guard(phantom, live_ctx)[0]['player_name'], "De'Anthony Melton")

print('== a game already STARTED is off the slate ==')
started = [{'away_team': 'Sacramento Kings',
            'home_team': 'Golden State Warriors',
            'game_id': '401909324', 'commence_time': PAST}]
chk('a finished game refuses its props', len(run_guard(phantom, started)), 0)

print('== a context row with NO start time still counts as playable ==')
# MLB context carries no start column at all; absence must not refuse.
nostart = [{'away_team': 'Sacramento Kings',
            'home_team': 'Golden State Warriors', 'game_id': '401909324'}]
chk('no start time -> on the slate', len(run_guard(phantom, nostart)), 1)

print('== ST. LOUIS: a period must not refuse a real game ==')
stl_prop = [{'matchup': 'St Louis Blues @ New York Islanders',
             'game_id': 'odds-hash', 'player_name': 'Jordan Kyrou'}]
stl_ctx = [{'away_team': 'St. Louis Blues',
            'home_team': 'New York Islanders',
            'game_id': '2026020111', 'commence_time': AHEAD}]
chk('props "St Louis" matches context "St. Louis"',
    len(run_guard(stl_prop, stl_ctx, sport='NHL', date='2026-11-25')), 1)

print('== the period strip must NOT merge two different teams ==')
mi_prop = [{'matchup': 'Arkansas @ Michigan', 'game_id': 'x',
            'player_name': 'nobody'}]
mi_ctx = [{'away_team': 'Arkansas', 'home_team': 'Michigan St.',
           'game_id': 'y', 'commence_time': AHEAD}]
chk('"Michigan" does not match "Michigan St."',
    len(run_guard(mi_prop, mi_ctx, sport='NCAAB', date='2026-11-26')), 0)

print('== NFL abbreviations join on BOTH keys ==')
nfl_prop = [{'matchup': 'KC @ BUF', 'game_id': 'nflkey1',
             'player_name': 'Josh Allen'}]
nfl_ctx = [{'away_team': 'KC', 'home_team': 'BUF',
            'game_id': 'nflkey1', 'kickoff_utc': AHEAD}]
chk('abbreviated matchup matches',
    len(run_guard(nfl_prop, nfl_ctx, sport='NFL', date='2026-11-26')), 1)
# game_id alone must be enough when the matchup text differs
nfl_prop2 = [{'matchup': 'Kansas City Chiefs @ Buffalo Bills',
              'game_id': 'nflkey1', 'player_name': 'Josh Allen'}]
chk('or the game_id alone carries it',
    len(run_guard(nfl_prop2, nfl_ctx, sport='NFL', date='2026-11-26')), 1)

print('== AN UNREADABLE SLATE MUST NOT EMPTY THE PROP PATH ==')
# Refusing on a context outage has a far bigger blast radius than the
# defect being guarded: it would silently remove every prop on every card
# for as long as the read fails. [[feedback_a_guards_blast_radius]]
chk('slate unknown (read returned None) -> rows pass unchecked',
    len(run_guard(phantom, None)), 2)

print('== a sport with no context table cannot be judged ==')
chk('unmapped sport passes its rows',
    len(run_guard(phantom, [], sport='UFC', date='2026-11-26')), 2)

print('== tonights_games: empty and unknown are DIFFERENT answers ==')
real = G.sb_get
try:
    G.sb_get = lambda tbl, params=None, **kw: []
    G._SLATE_CACHE.clear()
    mus, gids = G.tonights_games('NBA', '2026-11-26')
    chk('an empty read is an EMPTY set, not None', mus, set())
    G.sb_get = lambda tbl, params=None, **kw: None
    G._SLATE_CACHE.clear()
    mus2, _ = G.tonights_games('NBA', '2026-11-26')
    chk('a None read stays None', mus2, None)

    def _boom(tbl, params=None, **kw):
        raise RuntimeError('context read exploded')
    G.sb_get = _boom
    G._SLATE_CACHE.clear()
    mus3, _ = G.tonights_games('NBA', '2026-11-26')
    chk('a raising read is None, not empty', mus3, None)
finally:
    G.sb_get = real
    G._SLATE_CACHE.clear()

print('== THE WRITER: an ET day must not come from a -4h constant ==')
import nba_generate_props as NBP     # noqa: E402
import nhl_generate_props as NHP     # noqa: E402

for mod, nm in ((NBP, 'nba_generate_props'), (NHP, 'nhl_generate_props')):
    # 2026-11-26T04:21Z is 2026-11-25 23:21 ET in EST. The old constant
    # returned 11-26; the fixed one must return 11-25.
    chk('%s._et_date_of maps a 04:21Z commence to the prior ET day' % nm,
        mod._et_date_of('2026-11-26T04:21:00Z'), '2026-11-25')
    chk('%s._et_date_of: the two games that caused this (03:00Z)' % nm,
        mod._et_date_of('2026-11-26T03:00:00Z'), '2026-11-25')
    chk('%s._et_date_of: a 7pm ET tip (00:00Z next day)' % nm,
        mod._et_date_of('2026-11-27T00:00:00Z'), '2026-11-26')
    chk('%s._et_date_of: an afternoon game stays on its own day' % nm,
        mod._et_date_of('2026-11-26T18:00:00Z'), '2026-11-26')
    chk('%s._et_date_of: unparseable -> empty, never a guess' % nm,
        mod._et_date_of('not a timestamp'), '')
    chk('%s._et_date_of: None -> empty' % nm, mod._et_date_of(None), '')
    # EDT side: a July 04:21Z is still the previous ET day (00:21 EDT)
    chk('%s._et_date_of is right in EDT too' % nm,
        mod._et_date_of('2026-07-15T04:21:00Z'), '2026-07-15')
    chk('%s._et_date_of: EDT 03:00Z is the prior day' % nm,
        mod._et_date_of('2026-07-15T03:00:00Z'), '2026-07-14')
    chk('%s._et_today returns a real ISO date' % nm,
        len(mod._et_today()) == 10 and mod._et_today()[4] == '-', True)

print()
if fails:
    print('*** %d CASE(S) FAILED: %s ***' % (len(fails), ', '.join(fails)))
    print('    A failure here means a prop on a finished or unscheduled '
          'game can reach the card again.')
else:
    print('all prop slate guard cases pass')
raise SystemExit(1 if fails else 0)
