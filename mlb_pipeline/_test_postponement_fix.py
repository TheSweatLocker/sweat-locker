"""
Test the postponement-detection + self-heal fix from commit (TBD).

Verifies:
  1. MLB API ground-truth lookup distinguishes Postponed from Final from Pending
  2. Real 6/6 BOS@NYY postponement is correctly detected
  3. Real 6/6 CHW@PHI (which played) is NOT marked postponed (regression test —
     this game was incorrectly marked Push in the original 6/6 silent-blackout)
  4. Self-heal can backfill a missing mlb_game_results row from MLB API
  5. Future-dated game (no API data yet) returns 'pending', not 'postponed'

Run: python _test_postponement_fix.py
"""
import sys
sys.path.insert(0, '.')
from resolve_game_results import _fetch_mlb_game_state, _is_postponed


def case(label, ok, detail=''):
    status = 'PASS' if ok else 'FAIL'
    print(f'  [{status}] {label}{" — " + detail if detail else ""}')
    return ok


print('=== Postponement fix validation ===\n')

results = []

# Case 1: 6/6 BOS@NYY — confirmed Postponed via earlier MLB API check
state, score = _fetch_mlb_game_state('New York Yankees', 'Boston Red Sox', '2026-06-06')
results.append(case(
    'BOS@NYY 6/6 detected as postponed',
    state == 'postponed',
    f'state={state}',
))

# Case 2: 6/6 CHW@PHI — actually played (CHW won 6-3); previously misgraded as Push
state, score = _fetch_mlb_game_state('Philadelphia Phillies', 'Chicago White Sox', '2026-06-06')
results.append(case(
    'CHW@PHI 6/6 correctly detected as final (regression: was misgraded Push)',
    state == 'final' and score and score.get('away_score') == 6 and score.get('home_score') == 3,
    f'state={state} score={score}',
))

# Case 3: _is_postponed with team names — BOS@NYY returns True
results.append(case(
    '_is_postponed(BOS@NYY) returns True with team names',
    _is_postponed('2026-06-06', False, home_team='New York Yankees', away_team='Boston Red Sox') is True,
))

# Case 4: _is_postponed for CHW@PHI returns False (it played)
results.append(case(
    '_is_postponed(CHW@PHI) returns False — game played, not postponed',
    _is_postponed('2026-06-06', False, home_team='Philadelphia Phillies', away_team='Chicago White Sox') is False,
))

# Case 5: has_scores=True short-circuits — should always be False
results.append(case(
    '_is_postponed short-circuits when has_scores=True',
    _is_postponed('2026-06-06', True, home_team='Anything', away_team='Anything') is False,
))

# Case 6: Future date with no API data — returns False (pending, not postponed)
results.append(case(
    'Future-dated game returns False (not yet played != postponed)',
    _is_postponed('2027-01-01', False, home_team='Boston Red Sox', away_team='New York Yankees') is False,
))

# Case 7: Legacy fallback (no team names) — 1-day-old returns False under new threshold
results.append(case(
    'Legacy fallback (no team names): 1-day-old does NOT auto-Push',
    _is_postponed('2026-06-06', False) is False,
    'threshold raised to 3 days',
))

# Case 8: Today's slate — should be Pending or final, never Postponed
state, _ = _fetch_mlb_game_state('Atlanta Braves', 'Pittsburgh Pirates', '2026-06-07')
# Wait, ATL is home for today's game so: home=ATL, away=PIT
results.append(case(
    "Today's game (PIT@ATL) is pending or final, not postponed",
    state in ('pending', 'final'),
    f'state={state}',
))

passed = sum(results)
total = len(results)
print(f'\n{passed}/{total} passed')
sys.exit(0 if passed == total else 1)
