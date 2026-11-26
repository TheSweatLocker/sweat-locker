# -*- coding: utf-8 -*-
"""Regression: the LR shadow is RECORDED even when the model abstains.

== WHY THIS EXISTS (2026-11-26) ==========================================
`consistency_watchdog` reported a standing CRITICAL -- "NBA ML 4/9, NHL ML
8/14 ... games without a shadow silently no-op the LR gate" -- and it was
right. The cause was one early return in
`defensive_gates._apply_ml_lr_override_impl`:

    if pred['suggested_side'] == 'NONE':
        if was_ml:  ...records the shadow, demotes the tier...
        return pp          # a total or spread pick: NOTHING recorded

Correct about the PICK, which must not be demoted on a coin flip (Andy's
2026-10-29 ruling, written out at length in that file). Wrong about the
RECORD: with no `_lr_ml_shadow` key, the POTD's LR-confidence gate computed
`p_support = None` and admitted the candidate UNCHECKED, printing nothing.
Measured over six days: NBA spread picks carried no shadow on 23 of 25.

The two halves are tested together because fixing one without the other
is worse than neither:
  * the shadow must be recorded, or the gate stays blind;
  * a recorded p of ~0.50 must NOT be read as a 0.50 verdict, or the gate
    refuses every abstention -- the very error the ruling above forbids.

OFFLINE: stubs the model and the predictor, writes nothing.

    python test_lr_shadow_recorded.py
"""
import os
import sys

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import defensive_gates as DG    # noqa: E402

fails = []


def chk(name, got, want):
    ok = got == want
    print(('  PASS ' if ok else '  FAIL ') + name + '  got=%r want=%r'
          % (got, want))
    if not ok:
        fails.append(name)


COIN = {'suggested_side': 'NONE', 'p_home_win': 0.503,
        'model_version': 'test', 'suggested_tier': 'COVERAGE'}
PICKS = {'suggested_side': 'HOME', 'p_home_win': 0.71,
         'model_version': 'test', 'suggested_tier': 'STRONG'}
CTX = {'home_team': 'Houston Rockets', 'away_team': 'Phoenix Suns',
       'home_ml_close': -150, 'away_ml_close': 130, 'close_total': 228.5,
       'close_spread': -9.0}


def run(pp, pred):
    _real = DG._lr_predict_ml
    DG._lr_predict_ml = lambda ctx, model=None: dict(pred)
    try:
        return DG._apply_ml_lr_override_impl(dict(pp), dict(CTX),
                                             model=object(), sport='NBA')
    finally:
        DG._lr_predict_ml = _real


print('== an ABSTENTION on a non-ML pick: record it, change nothing ==')
spread = {'type': 'rl', 'tier': 'COVERAGE', 'side': 'HOME',
          'label': 'Houston Rockets -9', 'line': -9.0, 'conviction': 50}
out = run(spread, COIN)
chk('the shadow is recorded',
    (out.get('_lr_ml_shadow') or {}).get('p_home_win'), 0.503)
chk('and marked as an abstention', bool(out.get('_lr_abstained')), True)
chk('the tier is UNTOUCHED', out.get('tier'), 'COVERAGE')
chk('the label is UNTOUCHED', out.get('label'), 'Houston Rockets -9')
chk('the side is UNTOUCHED', out.get('side'), 'HOME')
chk('the type is UNTOUCHED', out.get('type'), 'rl')

total = {'type': 'total', 'tier': 'COVERAGE', 'side': 'OVER',
         'label': 'Over 6.5', 'line': 6.5, 'conviction': 51}
out = run(total, COIN)
chk('a TOTAL pick records it too',
    (out.get('_lr_ml_shadow') or {}).get('p_home_win'), 0.503)
chk('and its label is untouched', out.get('label'), 'Over 6.5')

print('== an abstention on an ML pick still demotes, as before ==')
ml = {'type': 'ml', 'tier': 'STRONG', 'side': 'HOME',
      'label': 'Houston Rockets ML', 'conviction': 70}
out = run(ml, COIN)
chk('ML abstention demotes to COVERAGE', out.get('tier'), 'COVERAGE')
chk('and records the shadow',
    (out.get('_lr_ml_shadow') or {}).get('p_home_win'), 0.503)

print('== a hand-set pick still outranks the model ==')
manual = dict(spread)
manual['_manual_correction'] = {'by': 'andy'}
out = run(manual, COIN)
chk('a manual marker blocks the override entirely',
    out.get('_lr_ml_shadow'), None)
chk('and the manual marker survives',
    bool(out.get('_manual_correction')), True)

print('== the POTD gate must DEMOTE an abstention, never refuse it ==')
# The gate's own arithmetic, as jerry_anchor_potd runs it. An abstention
# reaches the demotion path (p_support None); a real low reading refuses.
def p_support_of(pp, call_mkt, call_side):
    ml_shadow = pp.get('_lr_ml_shadow') or {}
    tot_shadow = pp.get('_lr_total_shadow') or {}
    if pp.get('_lr_abstained'):
        return None
    if call_mkt == 'ml' and ml_shadow.get('p_home_win') is not None:
        p = float(ml_shadow['p_home_win'])
        return p if call_side == 'HOME' else 1 - p
    if call_mkt == 'total' and tot_shadow.get('p_over') is not None:
        p = float(tot_shadow['p_over'])
        return p if call_side == 'OVER' else 1 - p
    if call_mkt in ('rl', 'spread') and ml_shadow.get('p_home_win') is not None:
        p = float(ml_shadow['p_home_win'])
        return p if call_side == 'HOME' else 1 - p
    return None


abstained = run(dict(spread), COIN)
chk('an abstained spread is UNJUDGED, not a 0.503 verdict',
    p_support_of(abstained, 'rl', 'HOME'), None)
decided = run(dict(spread), PICKS)
chk('a real reading IS judged',
    round(p_support_of(decided, 'rl', 'HOME') or 0, 3), 0.71)
chk('and a decided shadow carries no abstention mark',
    bool(decided.get('_lr_abstained')), False)
# the side the model did NOT pick must read as weak support, and be refused
chk('support flips for the away side',
    round(p_support_of(decided, 'rl', 'AWAY') or 0, 3), 0.29)
chk('which is below the 0.55 spread bar',
    (p_support_of(decided, 'rl', 'AWAY') or 0) < 0.55, True)

print()
if fails:
    print('*** %d CASE(S) FAILED: %s ***' % (len(fails), ', '.join(fails)))
    print('    A failure here means the LR gate is blind again, or is '
          'refusing on an abstention.')
else:
    print('all LR shadow recording cases pass')
raise SystemExit(1 if fails else 0)
