"""Unit tests for the cross-dimensional narrative validator (Phase 5).

Run: python _test_jerry_cross_dim_validator.py
"""
from generate_mlb_game_reads import (
    _classify_potd_dim,
    _detect_cross_dim_leakage,
)

passed = failed = 0

def check(name, actual, expected):
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")
        print(f"        expected: {expected!r}")
        print(f"        actual:   {actual!r}")

def check_count(name, actual_list, expected_count):
    global passed, failed
    if len(actual_list) == expected_count:
        passed += 1
        print(f"  PASS  {name} (errors={len(actual_list)})")
    else:
        failed += 1
        print(f"  FAIL  {name}")
        print(f"        expected {expected_count} errors, got {len(actual_list)}")
        for e in actual_list:
            print(f"        - {e}")


# ── classify_potd_dim ──
print("\n[classify_potd_dim]")

check("non-POTD returns None",
      _classify_potd_dim({"is_potd": False, "potd_lean": {"type": "ml"}}),
      None)

check("POTD ml → side",
      _classify_potd_dim({"is_potd": True, "potd_lean": {"type": "ml"}}),
      "side")

check("POTD ML → side",
      _classify_potd_dim({"is_potd": True, "potd_lean": {"type": "ML"}}),
      "side")

check("POTD over → total",
      _classify_potd_dim({"is_potd": True, "potd_lean": {"type": "over"}}),
      "total")

check("POTD under → total",
      _classify_potd_dim({"is_potd": True, "potd_lean": {"type": "under"}}),
      "total")

check("POTD spread → side",
      _classify_potd_dim({"is_potd": True, "potd_lean": {"type": "spread"}}),
      "side")

check("POTD rl → side",
      _classify_potd_dim({"is_potd": True, "potd_lean": {"type": "rl"}}),
      "side")

check("POTD prop → prop",
      _classify_potd_dim({"is_potd": True, "potd_lean": {"type": "prop_er_over"}}),
      "prop")

check("POTD missing potd_lean → None",
      _classify_potd_dim({"is_potd": True}),
      None)


# ── cross-dim leakage detector ──
print("\n[cross-dim — side POTD with total-only signals]")

side_struct = {"is_potd": True, "potd_lean": {"type": "ml"}}

# Side POTD citing "both pitchers elite" as rationale = bug
narr1 = "Take HOU ML — both pitchers elite, expect a low-scoring game."
check_count("'both pitchers elite' in side narrative", _detect_cross_dim_leakage(narr1, side_struct), 1)

# Side POTD citing NRFI lock as rationale = bug
narr2 = "Yankees ML on the NRFI lock signal."
check_count("'NRFI lock' in side narrative", _detect_cross_dim_leakage(narr2, side_struct), 1)

# Side POTD citing ace duel = bug
narr3 = "Mets ML — ace duel and the home team wins these."
check_count("'ace duel' in side narrative", _detect_cross_dim_leakage(narr3, side_struct), 1)

# Side POTD citing xERA gap = bug (it's a total signal)
narr4 = "Take Yankees -1.5 on the xERA gap."
check_count("'xERA gap' in side narrative", _detect_cross_dim_leakage(narr4, side_struct), 1)

# Side POTD citing legitimate side signal = OK
narr5 = "Yankees ML — confluence net +4 home, model spread 2.5 vs market 1.5, home dog autofade trap."
check_count("legitimate side reasoning OK", _detect_cross_dim_leakage(narr5, side_struct), 0)


print("\n[cross-dim — total POTD with side-only signals]")

total_struct = {"is_potd": True, "potd_lean": {"type": "over"}}

# Total POTD citing confluence net as rationale = bug
narr6 = "Take Over 8.5 — confluence net +4 supports it."
check_count("'confluence net' in total narrative", _detect_cross_dim_leakage(narr6, total_struct), 1)

# Total POTD citing autofade cohort = bug
narr7 = "Over 9.0 — autofade_dog_high_conv pattern hitting."
check_count("'autofade' in total narrative", _detect_cross_dim_leakage(narr7, total_struct), 1)

# Total POTD citing legitimate total signals = OK
narr8 = "Under 8.5 — ace duel, NRFI score 91, both starters elite, park 92 pitcher park, cold weather."
check_count("legitimate total reasoning OK", _detect_cross_dim_leakage(narr8, total_struct), 0)


print("\n[cross-dim — non-POTD reads skipped]")

# Non-POTD game read mentioning anything = OK (multi-dim by design)
nonpotd_struct = {"is_potd": False, "potd_lean": None}
mixed_narr = "Take Yankees ML and the Over — both pitchers elite, confluence net +4, NRFI lock."
check_count("non-POTD multi-dim narrative not validated", _detect_cross_dim_leakage(mixed_narr, nonpotd_struct), 0)


print("\n[cross-dim — prop POTD not enforced yet]")

prop_struct = {"is_potd": True, "potd_lean": {"type": "prop_er_over"}}
# Even if the narrative mentions totals/sides, we don't enforce on prop POTDs yet
prop_narr = "Painter ER Over 2.5 — both pitchers elite, confluence net +4."
check_count("prop POTD not enforced", _detect_cross_dim_leakage(prop_narr, prop_struct), 0)


print("\n[cross-dim — case insensitivity]")

ci_narr = "Take HOU ML — BOTH PITCHERS ELITE, expect a pitchers duel."
check_count("uppercase 'BOTH PITCHERS ELITE' caught", _detect_cross_dim_leakage(ci_narr, side_struct), 1)


print(f"\n{'='*40}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*40}")
exit(0 if failed == 0 else 1)
