"""Unit tests for the number-attribution validator.

Run: python _test_jerry_number_validator.py
"""
from generate_mlb_game_reads import (
    _walk_struct_numbers,
    _walk_struct_wl_pairs,
    _detect_number_hallucinations,
    _scrub_unverified_numbers,
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

# ── struct walkers ──
print("\n[struct number walker]")
struct = {
    "matchup": "Mets @ Reds",
    "market": {"close_total": 10.0, "model_total": 11.3},
    "pitchers": {"home": {"xera": 4.3, "k_pct": 24.5}},
    "cohort_signals": {
        "matched_plays": {
            "v3_tot": [{"shrunken_pct": 70.4, "raw": "28-11 lifetime (39 games)"}]
        }
    },
}
nums = _walk_struct_numbers(struct)
check("contains close_total 10.0", 10.0 in nums, True)
check("contains shrunken_pct 70.4", 70.4 in nums, True)
check("contains xera 4.3", 4.3 in nums, True)

pairs = _walk_struct_wl_pairs(struct)
check("extracts 28-11 from string", (28, 11) in pairs, True)

# ── number detector ──
print("\n[number detector — no errors expected]")
# All numbers in narrative are in struct
clean_narr = "Mets at +135 — model edge 11.3 vs 10.0 total. Cohort hits 70.4% historical (28-11 lifetime)."
check_count("clean narrative passes", _detect_number_hallucinations(clean_narr, struct), 0)

print("\n[number detector — should flag]")
# Invented 80%
bad_narr_1 = "Mets at +135 with 80% backtest hit rate."
errs = _detect_number_hallucinations(bad_narr_1, struct)
check_count("flags invented 80%", errs, 1)

# Invented W-L count
bad_narr_2 = "Cohort sits at 70% over 50-25 lifetime."  # 50-25 not in struct (28-11 is)
errs = _detect_number_hallucinations(bad_narr_2, struct)
check_count("flags invented 50-25", [e for e in errs if "50-25" in e], 1)

print("\n[number detector — tolerance for rounding]")
# 70% in narrative should match 70.4% in struct (within 2.5pp default)
round_narr = "Cohort at 70%."
check_count("70% matches 70.4% within tolerance", _detect_number_hallucinations(round_narr, struct), 0)

print("\n[number detector — hedging widens tolerance]")
# "approximately 75%" should match 70.4% (within 5pp hedged tolerance)
hedge_narr = "Cohort runs approximately 75% historical."
check_count("hedged 75% matches 70.4%", _detect_number_hallucinations(hedge_narr, struct), 0)

# But "approximately 90%" should NOT match (20pp off)
hedge_bad = "Cohort runs approximately 90% historical."
check_count("hedged 90% still flagged", _detect_number_hallucinations(hedge_bad, struct), 1)

print("\n[W-L: small pairs skipped (game scores)]")
score_narr = "Final score was 7-3 last night."  # not a cohort count
check_count("game score 7-3 NOT flagged", _detect_number_hallucinations(score_narr, struct), 0)

print("\n[W-L: ordering — both accepted]")
flip_narr = "11-28 lifetime."  # struct has 28-11, narrative inverts
check_count("flipped 11-28 NOT flagged", _detect_number_hallucinations(flip_narr, struct), 0)

print("\n[scrub function]")
errors = ["unverified percentage: '80%' (no struct value)"]
scrubbed = _scrub_unverified_numbers("Cohort hits 80% historical.", errors)
check("scrub replaces 80% with [unverified]", "[unverified]" in scrubbed, True)

print("\n[sample-size gating]")
# Struct with high-n W-L pair
high_n_struct = {
    "cohort_signals": {
        "matched_plays": {"v3_tot": [{"shrunken_pct": 70.4, "raw": "28-11 lifetime (39 games)"}]}
    },
}
# % with explicit n citation nearby — should pass
gated = "Cohort hits 70% across 39 games."
check_count("% with 'across 39 games' passes", _detect_number_hallucinations(gated, high_n_struct), 0)

# % with W-L count nearby — should pass (W-L is a proximity n signal)
wl_gated = "Cohort hits 70% — 28-11 lifetime."
check_count("% with W-L nearby passes", _detect_number_hallucinations(wl_gated, high_n_struct), 0)

# % WITHOUT any n citation, but struct has high-n backing — should pass
implicit_backed = "Cohort hits 70%."  # struct has 28-11 = 39 >= 30
check_count("% with high-n struct backing passes", _detect_number_hallucinations(implicit_backed, high_n_struct), 0)

# Struct WITHOUT any high-n backing
low_n_struct = {
    "cohort_signals": {
        "matched_plays": {"v3_tot": [{"shrunken_pct": 70.4, "raw": "5-2 lifetime (7 games)"}]}
    },
}
# % without nearby n AND no high-n struct backing — should FLAG as ungated
ungated = "Cohort hits 70%."
errs = _detect_number_hallucinations(ungated, low_n_struct)
flagged_ungated = [e for e in errs if "ungated" in e]
check_count("ungated % with low-n struct flagged", flagged_ungated, 1)

# Same % WITH nearby n — should pass even with low-n struct
gated_explicit = "Cohort hits 70% based on 7 games."
errs = _detect_number_hallucinations(gated_explicit, low_n_struct)
flagged_ungated = [e for e in errs if "ungated" in e]
check_count("low-n % with explicit n citation passes ungated check", flagged_ungated, 0)

print(f"\n{'='*40}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*40}")
exit(0 if failed == 0 else 1)
