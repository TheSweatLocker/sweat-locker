"""Tests for Phase 2.5 net-by-direction sweat scoring.

The bug the user flagged: conflicting signals (some Over, some Under)
were both pushing total_score up, making a coin-flip game read as
STRONG-tier conviction.

The fix nets directional drivers within a bucket so opposing signals
cancel out. Neutral drivers add to base as before.

Tested by mocking driver lists and calling the inline
_net_directional_score helper — Python doesn't expose it from
play_of_day directly since it's a closure, so we reimplement the
same logic here for assertion testing.
"""

def _net_directional_score(drivers, dir_a_vals, dir_b_vals):
    """Mirror of the helper inside play_of_day._compute_sweat_dimensions."""
    neutral_pts = sum(d.get('points', 0) for d in drivers
                      if not d.get('direction'))
    a_pts = sum(d.get('points', 0) for d in drivers
                if d.get('direction') in dir_a_vals)
    b_pts = sum(d.get('points', 0) for d in drivers
                if d.get('direction') in dir_b_vals)
    return min(100, max(0, 30 + neutral_pts + abs(a_pts - b_pts)))


passed = failed = 0

def check(name, actual, expected):
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"  PASS  {name}: {actual}")
    else:
        failed += 1
        print(f"  FAIL  {name}")
        print(f"        expected: {expected}")
        print(f"        actual:   {actual}")


# ── Test 1: directionally clean total (all Under signals) ──
print("\n[directionally clean — all signals point UNDER]")
clean_under = [
    {'points': 15, 'direction': 'UNDER', 'label': 'NRFI sweet spot'},
    {'points': 10, 'direction': 'UNDER', 'label': 'Ace duel'},
    {'points': 6, 'direction': 'UNDER', 'label': 'Pitcher park'},
    {'points': 3, 'direction': 'UNDER', 'label': 'Cold weather'},
]
# base 30 + neutral 0 + |0 - 34| = 64
check("clean UNDER total scores 64 (STRONG tier)",
      _net_directional_score(clean_under, ('OVER',), ('UNDER',)), 64)

# ── Test 2: conflicting total signals — should drop score ──
print("\n[conflicting signals — half Over, half Under]")
conflicting = [
    {'points': 15, 'direction': 'UNDER', 'label': 'NRFI sweet spot'},
    {'points': 10, 'direction': 'UNDER', 'label': 'Ace duel'},
    {'points': 9, 'direction': 'OVER', 'label': 'Coors park'},
    {'points': 12, 'direction': 'OVER', 'label': 'High wRC+ both teams'},
]
# OLD: 30 + 46 = 76 (PRIME)
# NEW: 30 + |21 - 25| = 34 (LIGHT — honest)
check("conflicting total scores 34 (LIGHT tier)",
      _net_directional_score(conflicting, ('OVER',), ('UNDER',)), 34)

# ── Test 3: neutral drivers add to base ──
print("\n[neutral drivers — no direction]")
mixed = [
    {'points': 15, 'direction': 'UNDER', 'label': 'NRFI'},
    {'points': 6, 'direction': None, 'label': 'xERA gap (no direction)'},
    {'points': 3, 'direction': None, 'label': 'wind no direction'},
]
# base 30 + neutral 9 + |0 - 15| = 54
check("neutral drivers contribute to base",
      _net_directional_score(mixed, ('OVER',), ('UNDER',)), 54)

# ── Test 4: side signals with HOME/AWAY ──
print("\n[side scoring — HOME vs AWAY net]")
side_clean_home = [
    {'points': 12, 'direction': 'HOME', 'label': 'PEAK confluence'},
    {'points': 6, 'direction': 'HOME', 'label': 'Confluence edge'},
]
check("clean HOME side scores 48 (LIGHT_LEAN tier)",
      _net_directional_score(side_clean_home, ('HOME',), ('AWAY',)), 48)

side_conflicting = [
    {'points': 12, 'direction': 'HOME', 'label': 'PEAK confluence'},
    {'points': 8, 'direction': 'AWAY', 'label': 'DOG RL signal'},
]
# 30 + |12 - 8| = 34
check("conflicting side scores 34 (PASS tier)",
      _net_directional_score(side_conflicting, ('HOME',), ('AWAY',)), 34)

# ── Test 5: empty drivers ──
print("\n[empty bucket]")
check("empty drivers = base 30",
      _net_directional_score([], ('OVER',), ('UNDER',)), 30)

# ── Test 6: cap at 100 ──
print("\n[cap at 100]")
huge = [{'points': 100, 'direction': 'UNDER', 'label': 'X'}]
check("score capped at 100",
      _net_directional_score(huge, ('OVER',), ('UNDER',)), 100)

# ── Test 7: max(0,...) floor ──
print("\n[floor at 0]")
negative_neutral = [
    {'points': -50, 'direction': None, 'label': 'cohort fade'},
]
check("negative neutral floors at 0",
      _net_directional_score(negative_neutral, ('OVER',), ('UNDER',)), 0)

# ── Test 8: real game — yesterday's BAL@SEA was STRONG OVER ──
print("\n[real game: BAL@SEA-style clean OVER]")
real_clean_over = [
    {'points': 14, 'direction': 'OVER', 'label': 'xERA gap'},  # ambiguous in reality but tag as OVER for test
    {'points': 5, 'direction': 'OVER', 'label': 'Park slight Over'},
    {'points': 8, 'direction': 'OVER', 'label': 'Cohort confirms OVER'},
]
# 30 + |27 - 0| = 57
check("clean OVER game scores 57 (LIGHT_LEAN tier)",
      _net_directional_score(real_clean_over, ('OVER',), ('UNDER',)), 57)


print(f"\n{'='*40}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*40}")
exit(0 if failed == 0 else 1)
