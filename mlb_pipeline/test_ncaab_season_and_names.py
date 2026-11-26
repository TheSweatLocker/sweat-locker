# -*- coding: utf-8 -*-
"""Regression: an NCAAB result row must carry `season`, and a rating
snapshot's team name must resolve.

== WHY THIS EXISTS (2026-11-26) ==========================================
Andy: "i need to make sure name matching is defct free for colleg
basketball ... i wnat to make sure ... situational ATS team recodrds ...
(same sitautional record swhon in eveyr sport is updating fo rthis season".

TWO DEFECTS, both invisible in output, both measured.

1. `season` WAS DERIVED AND THROWN AWAY. The 2026-11-20 commit f01e2df8
   ("NCAAB root cause: the resolver never wrote `season`") added
       'season': _season
   to the dict event_to_outcome returns -- and NEITHER write path carried
   it. upsert_new_games hand-rolls its own payload (it never listed
   season) and compute_outcome_patch builds another (same). So the value
   was computed on every run and discarded.

   Measured on 2026-11-26: 41 scored rows with season NULL, dated
   2026-11-20, 21, 22, 23, 24 and 25 -- i.e. EVERY date since that commit,
   and none before it, because the older 344 had been backfilled by hand.
   A fix whose only evidence is "the old rows are clean" cannot tell a
   working writer from a one-off repair.

   team_situational_records GROUPS BY season and the app queries
   season=eq.2026-27, so those games were in no team's record at all --
   265 cells parked under a NULL season key that no reader asks for.
   The ATS record is unaffected (an unlined game has no spread to grade)
   but the MONEYLINE record needs no line, so each one was a real
   straight-up result missing from a team's card.

2. HASLAM STORED XML-ESCAPED TEAM NAMES. ncaab_haslam_pull parses the feed
   with a regex, so `t="Texas A&amp;M"` was stored verbatim. 19 spellings
   did not resolve -- 8 from `&amp;` (Texas A&M, William & Mary, Florida
   A&M, Alabama A&M, NC A&T, PV A&M, East Texas A&M, Texas A&M-CC) and 11
   from an abbreviation style ('Cent. Michigan', 'Coast. Carolina',
   'W. Kentucky', 'LA Tech', 'UTRGV', 'Tarleton', "Saint Joe's",
   'Abil. Christian', 'Cent. Arkansas', 'Cent. Conn. St.',
   'S. Carolina St.').

   ncaab_panel_predictor resolves and keeps unresolvable names raw, so the
   orphan became its own team and the REAL team ran the panel on 2 of 3
   rating systems: 344 of 365 teams had all three, 21 had two.
   Fixed to 363/365 (Fairleigh Dickinson and Tennessee Tech carry no
   haslam row under any spelling, which is the feed's own omission).

   Note the split: the `&amp;` forms are OUR encoding bug and are fixed at
   the source with html.unescape; the abbreviations are real feed variants
   and belong in ncaab_team_aliases.alt_names. Putting an encoding bug in
   the alias table would have taught the resolver a spelling no human ever
   wrote.

OFFLINE: no DB, no network. Pure functions and source assertions.

    python test_ncaab_season_and_names.py
"""
import io
import os
import sys
from datetime import date

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('SUPABASE_URL', 'http://localhost')
os.environ.setdefault('SUPABASE_KEY', 'test')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY', 'test')

_HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def chk(name, got, want):
    ok = got == want
    print(('  PASS ' if ok else '  FAIL ') + name + '  got=%r want=%r'
          % (got, want))
    if not ok:
        fails.append(name)


def _src(fn):
    return io.open(os.path.join(_HERE, fn), encoding='utf-8').read()


print('== THE SEASON LABEL ITSELF ==============================')
from ncaab_season import season_label                        # noqa: E402

chk('a November game belongs to the season ENDING next year',
    season_label(date(2026, 11, 25)), '2026-27')
chk('a March game belongs to the season that began last autumn',
    season_label(date(2027, 3, 14)), '2026-27')
chk('August is the rollover, so 08-01 is already the new season',
    season_label(date(2026, 8, 1)), '2026-27')
chk('July is still the finishing season', season_label(date(2026, 7, 31)),
    '2025-26')

print('== BOTH WRITE PATHS must name season ====================')
import resolve_ncaab_results as RNR                          # noqa: E402

_RSRC = _src('resolve_ncaab_results.py')
_ins = _RSRC.split('def upsert_new_games')[1].split('\ndef ')[0]
chk('upsert_new_games payload names `season`', "'season'" in _ins, True)
_pat = _RSRC.split('def compute_outcome_patch')[1].split('\ndef ')[0]
chk('compute_outcome_patch can fill `season`', "'season'" in _pat, True)

print('== A NULL season IS WORK TO DO ===========================')
# A fully-scored, fully-graded row whose only gap is `season` must still
# produce a patch -- otherwise a row that reached the table NULL could
# never heal, which is how 41 of them accumulated.
new = {'game_id': 'g1', 'game_date': '2026-11-25', 'season': '2026-27',
       'home_score': 70, 'away_score': 60}
settled = {'game_id': 'g1', 'game_date': '2026-11-25', 'home_score': 70,
           'away_score': 60, 'close_spread': -5.0, 'close_total': 130.0,
           'spread_result': 'home_covered', 'total_result': 'over',
           'season': '2026-27'}
chk('nothing owed -> no patch', RNR.compute_outcome_patch(new, settled), None)

null_season = dict(settled, season=None)
p = RNR.compute_outcome_patch(new, null_season)
chk('a NULL season alone produces a patch', p is not None, True)
chk('and that patch sets the season', (p or {}).get('season'), '2026-27')

blank_season = dict(settled, season='')
chk('an empty-string season is treated as missing too',
    (RNR.compute_outcome_patch(new, blank_season) or {}).get('season'),
    '2026-27')

print('== A STORED season IS NEVER OVERWRITTEN ==================')
# Only ever FILL. A hand-corrected label must survive a resolver re-run.
disagree = dict(settled, season='2025-26')
chk('a different stored season is left alone',
    RNR.compute_outcome_patch(new, disagree), None)
unscored = dict(settled, season=None, home_score=99, away_score=11)
p2 = RNR.compute_outcome_patch(new, unscored)
chk('a score change still carries the season fill',
    (p2 or {}).get('season'), '2026-27')
chk('and never invents one when the date parse failed',
    'season' in (RNR.compute_outcome_patch(
        dict(new, season=None), null_season) or {}), False)

print('== THE HASLAM FEED: XML ENTITIES ARE DECODED =============')
import ncaab_haslam_pull as HP                               # noqa: E402

_XML = (
    '<mr rk="1" t="Texas A&amp;M" c="SEC" oe="118.1" de="95.2" '
    'ou="68.0" du="67.0"/>'
    '<mr rk="2" t="William &amp; Mary" c="CAA" oe="104.0" de="103.0"/>'
    '<mr rk="3" t="Duke" c="ACC" oe="125.0" de="90.0"/>'
)


class _Resp(object):
    status_code = 200
    text = _XML


_real_get = HP.requests.get
HP.requests.get = lambda *a, **k: _Resp()
try:
    teams = HP.fetch_haslam()
finally:
    HP.requests.get = _real_get

names = [t['team'] for t in teams]
chk('three rows parsed', len(teams), 3)
chk('`&amp;` is decoded to `&`', 'Texas A&M' in names, True)
chk('the escaped form is NOT stored', 'Texas A&amp;M' in names, False)
chk('a spaced ampersand decodes too', 'William & Mary' in names, True)
chk('a plain name is untouched', 'Duke' in names, True)
chk('the conference is decoded as well',
    [t['conference'] for t in teams if t['team'] == 'Texas A&M'], ['SEC'])
chk('adj_em is still off minus def',
    round([t['adj_em'] for t in teams if t['team'] == 'Duke'][0], 1), 35.0)

# ── THE 19 LIVE SPELLINGS ARE ASSERTED ELSEWHERE, ON PURPOSE ─────────
# ncaab_team_resolver loads ncaab_team_aliases FROM THE DATABASE, so a
# resolve() assertion here would need production credentials to import --
# the shape that failed every daily_card run for a day
# ([[feedback_exit_1_after_0s_is_an_import_crash]]). Stubbing the alias
# table instead would only prove the stub, which is a permanently-green
# check of nothing.
#
# So the live half is asserted by audit_ncaab_name_integrity.py, which
# reads the real table, exits 1 on any unresolvable or non-canonical name,
# and -- as of today -- covers ncaab_rating_snapshots. The 19 spellings
# added to alt_names on 2026-11-26 are:
#
#   Abil. Christian -> Abilene Christian      LA Tech -> Louisiana Tech
#   Cent. Arkansas  -> Central Arkansas       UTRGV -> UT Rio Grande Valley
#   Cent. Conn. St. -> Central Connecticut    Tarleton -> Tarleton St.
#   Cent. Michigan  -> Central Michigan       W. Kentucky -> W. Kentucky
#   Coast. Carolina -> Coastal Carolina       Saint Joe's -> Saint Joseph's
#   S. Carolina St. -> South Carolina St.
#   + the eight &amp; forms, kept as a safety net for rows already stored.
#
# Each was verified by ELIMINATION before being written: the spelling
# resolved to None, the target resolved to itself, and the target was one
# of the 21 canonical teams measurably missing a haslam rating. That last
# check is what makes the pairing a measurement and not my guess at a
# school's name ([[feedback_prefix_strip_can_name_another_school]]).

print('== THE NAME AUDIT MUST WATCH THE TABLE THAT FAILED =======')
# audit_ncaab_name_integrity passed clean all morning while
# ncaab_rating_snapshots held 19 unresolvable names, because that table
# was not in its list. A green check over the wrong surfaces is how this
# shipped ([[feedback_coverage_pct_hides_dead_code]]).
# Split on the list's own terminator at column 0, not on the first ']' --
# a [[memory-link]] inside a comment in the list carries brackets and
# truncated this extraction, which read as the audit missing a table.
_LIST_END = chr(10) + ']'
_surfaces = _src('audit_ncaab_name_integrity.py').split(
    'JOIN_SURFACES')[1].split(_LIST_END)[0]
for tbl in ('ncaab_rating_snapshots', 'team_situational_records'):
    chk('audit covers %s' % tbl, tbl in _surfaces, True)

print()
if fails:
    print('*** %d CASE(S) FAILED: %s ***' % (len(fails), ', '.join(fails)))
    print('    A failure here means an NCAAB game can fall out of every '
          'team record again, or a rating system can go dark for a team.')
else:
    print('all NCAAB season + name cases pass')
raise SystemExit(1 if fails else 0)
