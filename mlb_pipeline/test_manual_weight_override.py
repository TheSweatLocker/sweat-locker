# -*- coding: utf-8 -*-
"""Regression: a hand-set weight reaches a pick, and only where it should.

== WHY THIS EXISTS (2026-11-26) =========================================
Andy: "hand wigthing stats absed on previous matchups is legit data
analytics" -- correcting me after I said hand-setting a weight does
nothing. He was right on the analytics; matchup history is the best
measured family in this repo (MLB pitcher_vs_team_dominant_history 63.09%
n=401 +12.16pp, fifteen signals VALIDATED at 0.78-1.0 weight). What was
broken was the LEVER: `recommended_weight` is named once in
ensemble_scorer, inside a select, and its value is never read --
signal_registry_lookup.weight_of() is called nowhere in the repo. Editing
that column moved every audit number and zero picks.

20261126a adds signal_registry.manual_weight and this suite pins the
reader.

WHAT EACH GROUP GUARDS:

  0.0 IS A VALID OVERRIDE. "Mute this signal deliberately" has to be
  distinguishable from "no override set", or a falsy check invents the
  default. That exact bug shipped a tier and a confidence here before
  ([[feedback_falsy_zero_invented_a_tier_and_a_confidence]]).

  THE OPT-IN IS weight_source, NOT the number. A bare manual_weight with
  no source must not apply -- otherwise a half-written row, or a column
  default, silently reprices a board.

  THE EXPIRY IS ENFORCED, NOT REPORTED. Past weight_review_at the measured
  weight takes back over inside _manual_weight_of. An override with no end
  date is a dead value pricing live picks
  ([[feedback_a_dead_value_keyed_on_itself_is_immortal]]), and the boundary
  case -- review_at == today -- must still APPLY, because a window that is
  off by one day is off by a whole slate.

  ANTI_VALIDATED IS REFUSED, AND THE REFUSAL IS RECORDED. edge_weight
  hard-zeros a signal measured to be actively wrong, and a hand number must
  not outrank that ([[feedback_unmeasured_signal_must_not_outweigh_measured]]).
  But an override that is set and silently not firing is the worst state, so
  the refusal lands in _MANUAL_WEIGHT_APPLIED for the audit to surface
  ([[feedback_a_lens_can_be_dark_behind_a_404]]).

  THE FADE DOES NOT INHERIT IT. A fade bets the OTHER side, so a weight set
  for the signal is not a weight for its inverse -- the same reason the fade
  does not inherit price_be
  ([[feedback_check_which_side_a_column_indexes]]).

  OFF MEANS OFF, WITHOUT A NETWORK CALL. ENSEMBLE_MANUAL_WEIGHT=0 has to
  short-circuit the column probe before it widens the select, because a
  PostgREST 400 on an unknown column returns ZERO rows, an empty registry
  raises nothing, and every signal in six sports then falls to
  UNMEASURED_FLOOR ([[feedback_zero_rows_is_a_wiring_error]]).

OFFLINE: pure functions and _score_market. No DB, no network.

    python test_manual_weight_override.py
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


def close(name, got, want, tol=1e-6):
    ok = got is not None and abs(got - want) <= tol
    print(('  PASS ' if ok else '  FAIL ') + name + '  got=%r want~%r'
          % (got, want))
    if not ok:
        fails.append(name)


import ensemble_scorer as E                 # noqa: E402

TODAY = E._et_today_str()
PAST = '2020-01-01'
FUTURE = '2099-12-31'


def reg(**kw):
    """A registry row with the override fields, defaults sane."""
    r = {'signal_name': 's', 'sport': 'NCAAB', 'market_scope': 'total'}
    r.update(kw)
    return r


print('== THE ET DAY COMES FROM sweat_dates =====================')
chk('_et_today_str returns an ISO day', len(TODAY), 10)
chk('and it parses', TODAY[4] == '-' and TODAY[7] == '-', True)

print('== weight_source IS THE OPT-IN, NOT THE NUMBER ===========')
chk('no row at all', E._manual_weight_of(None), None)
chk('empty row', E._manual_weight_of({}), None)
chk('a number with NO source does not apply',
    E._manual_weight_of(reg(manual_weight=0.45)), None)
chk("source='measured' does not apply",
    E._manual_weight_of(reg(manual_weight=0.45,
                            weight_source='measured')), None)
chk("source='manual' with no number does not apply",
    E._manual_weight_of(reg(weight_source='manual')), None)
close("source='manual' + a number applies",
      E._manual_weight_of(reg(manual_weight=0.45,
                              weight_source='manual')), 0.45)
close('and the source is read case/space insensitively',
      E._manual_weight_of(reg(manual_weight=0.45,
                              weight_source='  MANUAL ')), 0.45)

print('== 0.0 IS A VALID OVERRIDE, NOT AN ABSENT ONE ============')
# [[feedback_falsy_zero_invented_a_tier_and_a_confidence]]
got = E._manual_weight_of(reg(manual_weight=0.0, weight_source='manual'))
chk('a deliberate 0.0 is NOT None', got is None, False)
close('and it is zero', got, 0.0)
close('1.0, the other boundary, applies',
      E._manual_weight_of(reg(manual_weight=1.0,
                              weight_source='manual')), 1.0)

print('== OUT OF RANGE IS A BAD ROW, NOT A WEIGHT ===============')
chk('1.5 refused (a percent pasted into a fraction)',
    E._manual_weight_of(reg(manual_weight=1.5,
                            weight_source='manual')), None)
chk('45 refused',
    E._manual_weight_of(reg(manual_weight=45,
                            weight_source='manual')), None)
chk('-0.1 refused',
    E._manual_weight_of(reg(manual_weight=-0.1,
                            weight_source='manual')), None)
chk('a non-numeric refused',
    E._manual_weight_of(reg(manual_weight='heavy',
                            weight_source='manual')), None)

print('== THE EXPIRY IS ENFORCED, AND THE BOUNDARY APPLIES ======')
chk('a past review date stops the override',
    E._manual_weight_of(reg(manual_weight=0.45, weight_source='manual',
                            weight_review_at=PAST)), None)
close('a future review date still applies',
      E._manual_weight_of(reg(manual_weight=0.45, weight_source='manual',
                              weight_review_at=FUTURE)), 0.45)
close('review_at == TODAY still applies (off-by-one is a whole slate)',
      E._manual_weight_of(reg(manual_weight=0.45, weight_source='manual',
                              weight_review_at=TODAY)), 0.45)
close('a timestamp-shaped review date is read by its date part',
      E._manual_weight_of(reg(manual_weight=0.45, weight_source='manual',
                              weight_review_at=FUTURE + 'T12:00:00+00:00')),
      0.45)
close('no review date = never lapses',
      E._manual_weight_of(reg(manual_weight=0.45, weight_source='manual',
                              weight_review_at=None)), 0.45)

print('== THE OPINION CARRIES IT, DEFAULTING TO ABSENT ==========')
chk('Opinion has the field',
    'manual_weight' in E.Opinion.__dataclass_fields__, True)
chk('and it defaults to None',
    E.Opinion.__dataclass_fields__['manual_weight'].default, None)


def score(manual=None, tier='VALIDATED', hr=0.56, n=66, mode='1'):
    """Drive the real scoring loop for one opinion. Returns (weight, log)."""
    old_mode, old_log = E._MANUAL_WEIGHT_MODE, list(E._MANUAL_WEIGHT_APPLIED)
    E._MANUAL_WEIGHT_MODE = mode
    del E._MANUAL_WEIGHT_APPLIED[:]
    try:
        op = E.Opinion(signal_key='h2h_under_streak', signal_class='h2h',
                       side='UNDER', strength=0.8, hit_rate=hr, sample_n=n,
                       tier=tier, display_prose='x', manual_weight=manual)
        d = E._score_market('total', [op], {'sport': 'NCAAB',
                                            'game_id': 'g1'}, sport='NCAAB')
        w = d.contributions[0].weight if d.contributions else None
        return w, list(E._MANUAL_WEIGHT_APPLIED)


# MAX_CLASS_SHARE caps any one signal_class at 40% of a side's total, and a
# lone opinion is 100% of its side -- so every final contribution weight here
# is its raw weight * 0.4. The override operates BEFORE that cap, which is
# why the raw value is asserted off the log and the capped one by ratio: a
# test that hardcoded 0.18 would be pinning the cap's constant, not the
# override ([[feedback_a_pair_needs_same_measure_and_scale]]).
    finally:
        E._MANUAL_WEIGHT_MODE = old_mode
        del E._MANUAL_WEIGHT_APPLIED[:]
        E._MANUAL_WEIGHT_APPLIED.extend(old_log)


print('== THE SCORER ACTUALLY USES IT ===========================')
w_measured, log0 = score(manual=None)
chk('with no override, nothing is logged', len(log0), 0)
chk('and the measured weight is what it was', w_measured is not None, True)
w_manual, log1 = score(manual=0.45)
chk('the application is logged', len(log1), 1)
chk('logged as applied', log1[0]['applied'], True)
close('the override IS the weight the scorer used', log1[0]['w_manual'], 0.45)
chk('and it replaced a DIFFERENT measured weight',
    abs(log1[0]['w_measured'] - 0.45) > 1e-6, True)
chk('which actually moved the final contribution',
    abs(w_manual - w_measured) > 1e-6, True)
# The class cap scales both runs by the same factor, so the ratio of final
# weights must equal the ratio of raw weights.
_raw_ratio = 0.45 / log1[0]['w_measured']
close('the move downstream is exactly proportional to the override',
      w_manual / w_measured, _raw_ratio, tol=2e-3)

print('== A LEVER CANNOT ESCAPE THE DOMINANCE CAP ===============')
# MAX_CLASS_SHARE exists so no single class carries a pick. An override that
# bypassed it would let one hand-set signal own a whole card -- the cohort
# failure of 2026-08-23 all over again, with Andy's own number on it.
w_max, log_max = score(manual=1.0)
close('a 1.0 override is still reported as 1.0 pre-cap',
      log_max[0]['w_manual'], 1.0)
chk('but the final contribution is capped below it', w_max < 1.0, True)
chk('and it is still the largest weight we have seen',
    w_max > w_manual, True)

print('== A 0.0 OVERRIDE MUTES, AND IS NOT "NO OVERRIDE" ========')
w_mute, log_mute = score(manual=0.0)
chk('the signal is muted (weight 0 or dropped)',
    w_mute in (0.0, None), True)
chk('and the mute was logged as an application', len(log_mute), 1)

print('== ANTI_VALIDATED IS REFUSED, AND THE REFUSAL RECORDED ===')
w_anti_base, _ = score(manual=None, tier='ANTI_VALIDATED')
w_anti, log_anti = score(manual=0.90, tier='ANTI_VALIDATED')
chk('a hand weight cannot resurrect ANTI_VALIDATED',
    w_anti, w_anti_base)
chk('the override did NOT become the weight', w_anti == 0.90, False)
chk('and the refusal is recorded, not dropped', len(log_anti), 1)
chk('marked not applied', log_anti[0]['applied'], False)
chk('with the reason', log_anti[0]['why'], 'ANTI_VALIDATED')

print('== OFF MEANS OFF ========================================')
w_off, log_off = score(manual=0.45, mode='0')
close('with the mode off the measured weight stands', w_off, w_measured)
chk('and nothing is logged', len(log_off), 0)

print('== OFF SHORT-CIRCUITS THE PROBE, NO NETWORK =============')
# A 400 on an unknown column returns ZERO rows, an empty registry raises
# nothing, and six sports then price off the 0.20 UNMEASURED_FLOOR
# ([[feedback_zero_rows_is_a_wiring_error]]). So the select must only widen
# behind this probe, and the probe must not even try when the mode is off.
_om, _oc = E._MANUAL_WEIGHT_MODE, E._MANUAL_W_COL_OK
try:
    E._MANUAL_WEIGHT_MODE = '0'
    E._MANUAL_W_COL_OK = None
    chk('mode off -> probe False with no request',
        E._manual_weight_column_exists(), False)
    E._MANUAL_WEIGHT_MODE = 'off'
    E._MANUAL_W_COL_OK = None
    chk("mode 'off' too", E._manual_weight_column_exists(), False)
finally:
    E._MANUAL_WEIGHT_MODE, E._MANUAL_W_COL_OK = _om, _oc

print('== DEFAULT IS ON, AND SAFE BECAUSE NOTHING IS SET ========')
chk('the shipped default is on',
    os.environ.get('ENSEMBLE_MANUAL_WEIGHT', '1').strip().lower()
    in ('1', 'on', 'true'), True)
chk('and with no override a row yields None (so no pick moves)',
    E._manual_weight_of(reg(recommended_weight=0.8)), None)

print('== THE FADE MUST NOT INHERIT IT =========================')
# A fade bets the OTHER side; a weight set for the signal is not a weight
# for its inverse ([[feedback_check_which_side_a_column_indexes]]).
import io as _io                                                # noqa: E402
_src = _io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'ensemble_scorer.py'), encoding='utf-8').read()
_fade_blocks = _src.count("__fade',")
chk('there are fade Opinion constructions to check', _fade_blocks >= 1, True)
for _marker in ("signal_key=f'{source[\"signal_key\"]}__fade'",
                "signal_key=f'{source_row[\"signal_key\"]}__fade'"):
    if _marker in _src:
        _after = _src.split(_marker, 1)[1].split('))', 1)[0]
        chk('the fade construction passes no manual_weight',
            'manual_weight=' in _after, False)
        chk('and no price_be either (same reason)',
            'price_be=' in _after, False)

print()
if fails:
    print('*** %d CASE(S) FAILED: %s ***' % (len(fails), ', '.join(fails)))
    print('    A failure here means a hand-set weight either does not reach '
          'a pick -- the defect this replaced -- or reaches one it must not.')
else:
    print('all manual weight override cases pass')
raise SystemExit(1 if fails else 0)
