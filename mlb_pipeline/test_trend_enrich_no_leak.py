# -*- coding: utf-8 -*-
"""Regression: a season aggregate must never be stamped on a finished game.

== WHY THIS EXISTS (2026-11-26) ==========================================
Andy: "change weitghts lets worok it". Chasing why four NCAAB
*_trend_season signals carried WEIGHT 1.0 at 56.2-60.1% when a
walk-forward control could not clear 2 SE in twelve variants.

THE LEAK. team_season_trends holds each team's O/U and ATS record AS OF
NOW. enrich_team_trends writes it onto the rows of one game_date. Run
while that date's games are finishing -- which is when the pipelines run
it, late in the ET day -- it stamps a POST-game aggregate onto a PRE-game
row, putting the game's own result inside the feature meant to predict it.
The *_trend_season signals read exactly those columns
(fix_signal_prose_ou_trend maps them to {side}_season_over_pct).

MEASURED on ncaab_game_context, comparing every stored O/U sample against
the games that had actually FINISHED before that date:

    1,030 of 1,583 team-rows (65.1%) stored MORE games than had been played

    +1..+5    853 rows   the aggregate includes the game itself.
                         Michigan St. 11-05: overs+unders=1, 0 games
                         finished before it, trends_updated 2026-11-06.
    +27..+39  ~177 rows  2026-11-02 rows carrying LAST season's full
                         31-34 game aggregate on opening week
                         ([[feedback_a_rolling_window_outlives_the_season]]).

TWO REFUSALS, because neither covers the other:

  1. A PAST game_date can never be pre-game. Every workflow caller runs
     with no --date, so this guards manual and backfill invocations only --
     measured blast radius zero.
  2. Within today, a game that is already FINAL. This is the one that
     bites: today's NCAAB slate went from 24 rows enriched to 13, skipping
     the 11 that had finished.

WHY THE RESULTS TABLE AND NOT THE CLOCK. ncaab_game_context stores
`game_time_et` carrying a `+00:00` offset -- a name and an offset that
disagree -- and MLB's context table has no start-time column at all.
Reading that wrong either skips a whole slate or none of it. A stored
SCORE is unambiguous ([[feedback_resolve_the_slate_not_the_clock]]).

Matched on an UNORDERED team pair: 42 NCAAB neutral-site games this season
are stored in the opposite orientation to their context row, and an
ordered match would miss every one
([[feedback_orientation_is_not_a_fact_at_a_neutral_site]]).

AND IF THE RESULTS PROBE FAILS, NOTHING IS ENRICHED. Passing the rows
through on an unreadable probe is what a prop-slate guard does, because
refusing there empties a card. Here the asymmetry runs the other way: a
missed enrichment costs one day of badge freshness, while a wrong one
corrupts a training feature permanently. So this guard fails CLOSED
([[feedback_a_guards_blast_radius]]).

OFFLINE: stubs requests. Writes nothing, reads nothing.

    python test_trend_enrich_no_leak.py
"""
import os
import sys

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('SUPABASE_URL', 'http://localhost')
os.environ.setdefault('SUPABASE_KEY', 'test')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY', 'test')

fails = []


def chk(name, got, want):
    ok = got == want
    print(('  PASS ' if ok else '  FAIL ') + name + '  got=%r want=%r'
          % (got, want))
    if not ok:
        fails.append(name)


import enrich_team_trends as E            # noqa: E402

TRENDS = [
    {'team': 'Michigan St.', 'over_pct': 40.0, 'ou_overs': 2,
     'ou_unders': 3, 'cover_pct': 50.0, 'ats_wins': 2, 'ats_losses': 2},
    {'team': 'Arkansas', 'over_pct': 60.0, 'ou_overs': 3, 'ou_unders': 2,
     'cover_pct': 60.0, 'ats_wins': 3, 'ats_losses': 2},
    {'team': 'Duke', 'over_pct': 50.0, 'ou_overs': 2, 'ou_unders': 2,
     'cover_pct': 50.0, 'ats_wins': 2, 'ats_losses': 2},
    {'team': 'Connecticut', 'over_pct': 50.0, 'ou_overs': 2, 'ou_unders': 2,
     'cover_pct': 50.0, 'ats_wins': 2, 'ats_losses': 2},
]
CTX = [
    {'game_id': 'g_live', 'home_team': 'Michigan St.', 'away_team': 'Arkansas'},
    {'game_id': 'g_done', 'home_team': 'Duke', 'away_team': 'Connecticut'},
]


class _R(object):
    def __init__(self, body, code=200):
        self._b, self.status_code = body, code

    def json(self):
        return self._b


def run(results_body, results_code=200, date=None, ctx=None):
    """Drive run_for_sport with stubbed reads. Returns (patched, out)."""
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if 'team_season_trends' in url:
            return _R(list(TRENDS))
        if 'ncaab_game_context' in url:
            return _R(list(ctx if ctx is not None else CTX))
        if 'ncaab_game_results' in url:
            return _R(results_body, results_code)
        return _R([], 404)

    real = E.requests.get
    E.requests.get = fake_get
    import io as _io
    import contextlib
    buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            n, _h, _a = E.run_for_sport(
                'NCAAB', date or E._et_today(), '2026-27', dry_run=True)
    finally:
        E.requests.get = real
    return n, buf.getvalue()


FINAL_DUKE = [{'home_team': 'Duke', 'away_team': 'Connecticut',
               'home_score': 77, 'away_score': 63}]

print('== A FINISHED GAME IS SKIPPED, AN UNPLAYED ONE IS NOT ====')
n, out = run(FINAL_DUKE)
chk('one of two games enriched', n, 1)
chk('and the skip is reported', 'skipped 1 game(s) already FINAL' in out, True)

print('== ORIENTATION MUST NOT MATTER (neutral sites) ===========')
swapped = [{'home_team': 'Connecticut', 'away_team': 'Duke',
            'home_score': 63, 'away_score': 77}]
n2, _ = run(swapped)
chk('the reversed stored orientation still counts as final', n2, 1)

print('== A SCORELESS RESULTS ROW IS NOT A FINISHED GAME ========')
pending = [{'home_team': 'Duke', 'away_team': 'Connecticut',
            'home_score': None, 'away_score': None}]
n3, _ = run(pending)
chk('a row with no score does not block enrichment', n3, 2)

print('== NOTHING FINAL -> EVERYTHING ENRICHED ==================')
n4, out4 = run([])
chk('an empty results day enriches both', n4, 2)
chk('and says nothing about skipping',
    'already FINAL' in out4, False)

print('== A PAST DATE IS REFUSED OUTRIGHT =======================')
n5, out5 = run([], date='2020-01-01')
chk('no game on a past date is enriched', n5, 0)
chk('and the reason is printed', 'is in the PAST' in out5, True)

print('== THE PROBE FAILING FAILS CLOSED ========================')
n6, out6 = run([], results_code=500)
chk('an unreadable results probe enriches NOTHING', n6, 0)
chk('and says why', 'cannot tell which games are final' in out6, True)

print('== THE TWO TABLE MAPS MUST NOT CROSS =====================')
# A duplicate key while patching these put nba_game_context into
# RESULTS_TABLE, where the last key wins
# ([[feedback_a_duplicate_yaml_key_deregisters_the_workflow]]).
chk('every RESULTS_TABLE value is a results table',
    all('_game_results' in v for v in E.RESULTS_TABLE.values()), True)
chk('every CTX_TABLE value is a context table',
    all('_game_context' in v for v in E.CTX_TABLE.values()), True)
chk('NBA is present in both', ('NBA' in E.CTX_TABLE
                               and 'NBA' in E.RESULTS_TABLE), True)
chk('and every context sport can be checked for finals',
    sorted(set(E.CTX_TABLE) - set(E.RESULTS_TABLE)), [])

print()
if fails:
    print('*** %d CASE(S) FAILED: %s ***' % (len(fails), ', '.join(fails)))
    print('    A failure here means a team season aggregate can be written '
          'onto a game already played, putting the result inside its own '
          'predictor.')
else:
    print('all trend enrichment leak cases pass')
raise SystemExit(1 if fails else 0)
