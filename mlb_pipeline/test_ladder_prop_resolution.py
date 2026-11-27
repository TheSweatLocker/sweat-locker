# -*- coding: utf-8 -*-
"""Regression: every sport's prop rung must be resolvable, and loudly.

== WHY THIS EXISTS (2026-11-27) ==========================================
Andy: "WHat about the ladder?" The ladder's last graded result was
2026-11-06. Five rungs sat `result=NULL` -- 4 NBA props (11-11..11-18) and
1 NFL prop (11-26) -- while EVERY ONE of their source prop rows was
already graded:

    ladder_rung id=72  11-26 NFL  "Patrick Mahomes Over 1.5 PASS TDS"  NULL
      nfl_pipeline_props id=79801  over 1.5 pass_tds_over  PRIME conv=97  Loss

A PRIME conv-97 play that LOST was displayed in-app as pending for a day.

THREE STACKED BREAKPOINTS, each returning None with no error:

  1. SPORT. `_grade_prop_rung`'s two lookups queried only
     `mlb_pipeline_props` and `prop_playbook_decisions`. The qualifier
     (`steam_room_ladder.py`) runs from cross_sport_lines.yml and scans
     every sport with sport_registry.ladder_eligible, so it has been
     locking NBA and NFL rungs since November. The resolver never
     followed it across.
  2. TOKEN. `_PROP_TYPE_MAP` hand-listed 14 tokens. The four props tables
     carry 64 distinct prop_type values, so 50 of them bailed at
     `if not prop_family: return None`. threes, steals, blocks and
     pass_tds -- every token on the five stuck rungs -- were missing.
     The map was never needed: steam_room_ladder composes the label with
     a transform that is exactly invertible, verified round-trip on all
     64 (prop_type, direction) pairs live, 0 failures.
  3. REGEX. The token group was `([A-Za-z_]+)\s*$`, which cannot match a
     token containing a space, so "Patrick Mahomes Over 1.5 PASS TDS"
     failed `re.match` outright before any lookup ran.

AND IT WAS SILENT. A 2026-10-08 fix made the loop PRINT every rung it
declined -- the right instinct, and it still exited 0. run_step.sh records
a step by its exit code, so five rungs stuck for 16 days produced no
step_failed row and no heartbeat: nightly_cross_sport ran clean every
night while the ladder was frozen
([[feedback_read_step_failures_from_the_db]]).

PLUS --since BOUNDED THE PENDING FETCH at 14 days back, so the 11-11 rung
had already aged out of the retry window permanently
([[feedback_a_rolling_window_needs_a_season_floor]]).

WHY THE INVERSE AND NOT A WIDER MAP. A map of a deterministic function is
a copy that drifts: this one covered 14 of 64 and said nothing about the
other 50. The guard below asserts the inverse holds for every sport's
token shape, so a new prop family cannot go dark by omission
([[feedback_measure_the_rule_you_are_coding]]).

OFFLINE: imports the module with stubbed env and requests. Writes
nothing, reads nothing.

    python test_ladder_prop_resolution.py
"""
import os
import sys
import types

os.environ.setdefault('SUPABASE_URL', 'http://stub.invalid')
os.environ.setdefault('SUPABASE_KEY', 'stub')

# Stub requests BEFORE import so module-level config cannot reach a network.
_stub = types.ModuleType('requests')


class _Resp:
    status_code = 200

    def json(self):
        return []


_stub.get = lambda *a, **k: _Resp()
_stub.patch = lambda *a, **k: _Resp()
_stub.post = lambda *a, **k: _Resp()
sys.modules['requests'] = _stub

import resolve_ladder_results as R   # noqa: E402

fails = []


def chk(name, got, want):
    ok = got == want
    print(('  ok   ' if ok else '  FAIL ') + name)
    if not ok:
        print('         got  %r' % (got,))
        print('         want %r' % (want,))
        fails.append(name)


print('1. every ladder-eligible sport that has props has a props table')
# NCAAF/NCAAB are deliberately absent: college publishes no props
# (feedback_college_sports_no_props). A prop rung there is an upstream bug,
# and _props_table returning None puts it in the ungradeable report.
chk('MLB', R._props_table('MLB'), 'mlb_pipeline_props')
chk('NFL', R._props_table('NFL'), 'nfl_pipeline_props')
chk('NBA', R._props_table('NBA'), 'nba_pipeline_props')
chk('NHL', R._props_table('NHL'), 'nhl_pipeline_props')
chk('lower-case sport still resolves', R._props_table('nba'), 'nba_pipeline_props')
chk('NCAAB has no props table', R._props_table('NCAAB'), None)
chk('an unknown sport does not raise', R._props_table('XFL'), None)

print()
print('2. the five rungs that were stuck now parse to their real prop_type')
# Each (label, expected prop_type) pair was confirmed against the graded
# row in that sport's props table.
STUCK = [
    ('Jerami Grant Under 1.5 THREES',        'threes_under'),
    ('Brandin Podziemski Under 2.5 THREES',  'threes_under'),
    ('Klay Thompson Under 0.5 BLOCKS',       'blocks_under'),
    ('Cameron Boozer Under 0.5 STEALS',      'steals_under'),
    ('Patrick Mahomes Over 1.5 PASS TDS',    'pass_tds_over'),
]
for label, want in STUCK:
    parsed = R._parse_prop_pick(label)
    if not parsed:
        chk('parses %r' % label, None, want)
        continue
    _player, direction, _line, token = parsed
    chk('%r -> %s' % (label, want),
        R._prop_type_from_token(token, direction), want)

print()
print('3. the MLB labels that already worked must keep working')
LEGACY = [
    ('Eduardo Rodriguez Over 5.5 HA',     'Eduardo Rodriguez', 'over', 5.5, 'ha_over'),
    ('Grayson Rodriguez Under 15.5 OUTS', 'Grayson Rodriguez', 'under', 15.5, 'outs_under'),
    ('Tarik Skubal Over 6.5 KS',          'Tarik Skubal', 'over', 6.5, 'ks_over'),
    ('Shawn Armstrong Under 0.5 ER',      'Shawn Armstrong', 'under', 0.5, 'er_under'),
    ('Mike Yastrzemski Under 0.5 HITS',   'Mike Yastrzemski', 'under', 0.5, 'hits_under'),
]
for label, player, direction, line, ptype in LEGACY:
    parsed = R._parse_prop_pick(label)
    chk('parses %r' % label, parsed is not None, True)
    if not parsed:
        continue
    chk('  player', parsed[0], player)
    chk('  direction', parsed[1], direction)
    chk('  line', parsed[2], line)
    chk('  prop_type', R._prop_type_from_token(parsed[3], parsed[1]), ptype)

print()
print('4. multi-word tokens of every shape the four tables carry')
# These are real prop_type values; the inverse must round-trip each.
for ptype, direction in [
    ('pass_tds_over', 'over'), ('pass_completions_over', 'over'),
    ('pass_attempts_under', 'under'), ('pass_yds_over', 'over'),
    ('rush_rec_yds_over', 'over'), ('pra_over', 'over'),
    ('total_bases_under', 'under'), ('saves_over', 'over'),
]:
    pretty = (ptype.replace('_', ' ')
                   .replace(' over', '').replace(' under', ''))
    token = pretty.upper()
    chk('%s -> %r -> back' % (ptype, token),
        R._prop_type_from_token(token, direction), ptype)

print()
print('5. a direction word inside a surname is not the direction')
# The player group is lazy and the direction is whitespace-bounded, so a
# surname like Overton cannot be eaten as the direction.
p = R._parse_prop_pick('Someone Overton Over 2.5 PASS COMPLETIONS')
chk('Overton parses', p is not None, True)
if p:
    chk('  player keeps the surname', p[0], 'Someone Overton')
    chk('  direction is the standalone word', p[1], 'over')
    chk('  prop_type', R._prop_type_from_token(p[3], p[1]),
        'pass_completions_over')

print()
print('6. garbage is refused rather than guessed')
chk('empty', R._parse_prop_pick(''), None)
chk('None', R._parse_prop_pick(None), None)
chk('a side, not a prop', R._parse_prop_pick('Kansas ML'), None)
chk('no line', R._parse_prop_pick('Someone Over KS'), None)
chk('no token', R._parse_prop_pick('Someone Over 1.5'), None)
chk('empty token -> no prop_type', R._prop_type_from_token('', 'over'), None)
chk('whitespace token -> no prop_type',
    R._prop_type_from_token('   ', 'over'), None)

print()
print('7. result strings from every writer normalise')
for raw, want in [('Win', 'Win'), ('WIN', 'Win'), ('W', 'Win'),
                  ('Loss', 'Loss'), ('LOSS', 'Loss'), ('L', 'Loss'),
                  ('Push', 'Push'), ('P', 'Push'),
                  ('Void', 'Push'), ('V', 'Push'),
                  ('', None), (None, None), ('Pending', None)]:
    chk('%r -> %r' % (raw, want), R._norm_result(raw), want)

print()
print('8. a stale ungraded rung must be able to fail the step')
chk('STALE_GRACE_DAYS is set and small', 0 < R.STALE_GRACE_DAYS <= 3, True)
# The module uses `from __future__ import annotations`, so the annotation
# arrives as a string rather than the type object.
chk('run() returns the pair main() exits on',
    str(R.run.__annotations__.get('return')), 'tuple')

print()
print('9. a --dry-run writes nothing')
# The first dry run of this fix PATCHed four rungs' odds before anything
# was approved. The values were right, which is why it was easy to miss.
_patched = []
_stub.patch = lambda *a, **k: (_patched.append(a), _Resp())[1]
R._backfill_rung_odds({'id': 1, 'odds_american': None},
                      {'book_over_odds': -150, 'book_under_odds': 120},
                      'over', dry_run=True)
chk('dry run issued no PATCH', _patched, [])
R._backfill_rung_odds({'id': 1, 'odds_american': -110},
                      {'book_over_odds': -150, 'book_under_odds': 120},
                      'over', dry_run=False)
chk('a stored price is never overwritten', _patched, [])

print()
if fails:
    print('*** %d CASE(S) FAILED: %s ***' % (len(fails), ', '.join(fails)))
    print('    A failure here means a ladder rung can sit ungraded while '
          'its source prop is settled, which freezes the published ladder '
          'record and can show a losing play as pending.')
else:
    print('all ladder prop-resolution cases pass')
raise SystemExit(1 if fails else 0)
