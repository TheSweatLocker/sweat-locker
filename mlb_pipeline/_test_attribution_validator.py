"""
Unit test for the attribution validator in generate_mlb_game_reads.

Runs against four scenarios:
  1. Vazquez/Padres recurring case (6/6 report)
  2. Tolle/Boston historical case (5/16)
  3. Benge/Nationals historical case (5/20)
  4. Today's clean Padres/Mets read (must NOT flag)
  5. Today's clean SF/CHC PRIME read (must NOT flag)

Run: python _test_attribution_validator.py
"""
import sys
sys.path.insert(0, ".")
from generate_mlb_game_reads import _detect_attribution_errors


def case(label, narrative, struct, expect_error_substr=None, expect_clean=False):
    errors = _detect_attribution_errors(narrative, struct)
    if expect_clean:
        if errors:
            print(f"❌ {label}: expected CLEAN, got errors:")
            for e in errors:
                print(f"     - {e}")
            return False
        print(f"✅ {label}: clean (as expected)")
        return True
    if not errors:
        print(f"❌ {label}: expected error matching '{expect_error_substr}', got none")
        return False
    matched = any(expect_error_substr.lower() in e.lower() for e in errors)
    if matched:
        print(f"✅ {label}: caught — {errors[0]}")
        return True
    print(f"❌ {label}: errors found but none matched '{expect_error_substr}':")
    for e in errors:
        print(f"     - {e}")
    return False


# ---- Case 1: Vazquez/Padres ----
# Hypothetical bad narrative — Patrick Vazquez plays for SD, Mets visit
case1_struct = {
    "matchup": "New York Mets @ San Diego Padres",
    "pitchers": {
        "home": {"name": "Patrick Vazquez", "own_team": "San Diego Padres"},
        "away": {"name": "Nolan McLean", "own_team": "New York Mets"},
    },
    "best_plays": [],
}
case1_bad = (
    "**The Setup**\n\nVazquez takes the mound facing the Padres lineup in a "
    "tough Petco park environment."
)

# ---- Case 2: Tolle/Boston (5/16 documented) ----
# Original incident: Tolle pitched FOR Boston (not Atlanta). The bad
# narrative described "Boston's 90 wRC+ lineup Tolle will punish" — but
# Tolle is on Boston, he doesn't face them.
case2_struct = {
    "matchup": "Atlanta Braves @ Boston Red Sox",
    "pitchers": {
        "home": {"name": "Spencer Tolle", "own_team": "Boston Red Sox"},
        "away": {"name": "Spencer Strider", "own_team": "Atlanta Braves"},
    },
    "best_plays": [],
}
case2_bad = (
    "Boston's 90 wRC+ lineup Tolle will punish in this matchup — "
    "the Red Sox bats can't handle his K stuff."
)

# ---- Case 3: Benge/Nationals (5/20 documented) ----
case3_struct = {
    "matchup": "New York Mets @ Washington Nationals",
    "pitchers": {
        "home": {"name": "Zach Davies", "own_team": "Washington Nationals"},
        "away": {"name": "Sean Manaea", "own_team": "New York Mets"},
    },
    "best_plays": [
        {"player": "Carson Benge", "team": "New York Mets", "prop_type": "hits_over_05"},
        {"player": "Juan Soto",    "team": "New York Mets", "prop_type": "hits_over_05"},
    ],
}
case3_bad = (
    "Two Nationals hitters Benge and Soto both look strong on hits over against "
    "Davies' fade-prone profile."
)

# ---- Case 4: today's actual clean Padres/Mets read ----
case4_struct = {
    "matchup": "New York Mets @ San Diego Padres",
    "pitchers": {
        "home": {"name": "Griffin Canning", "own_team": "San Diego Padres"},
        "away": {"name": "Nolan McLean", "own_team": "New York Mets"},
    },
    "best_plays": [
        {"player": "Xander Bogaerts",  "team": "San Diego Padres"},
        {"player": "Sung-Mun Song",    "team": "San Diego Padres"},
        {"player": "Rodolfo Durán",    "team": "San Diego Padres"},
    ],
}
case4_clean = (
    "Mets @ Padres at Petco Park grades as a HIGH-conviction lean to New York. "
    "McLean's xERA (3.38) sits well ahead of Canning's (4.97), and the Mets' "
    "bullpen outranks San Diego's. Three Padres hitters surface: Xander Bogaerts, "
    "Sung-Mun Song, and Rodolfo Durán all grade STRONG on hits under 0.5."
)

# ---- Case 5: SF/CHC PRIME — pitcher possessive case shouldn't false-flag ----
case5_struct = {
    "matchup": "San Francisco Giants @ Chicago Cubs",
    "pitchers": {
        "home": {"name": "Cade Horton", "own_team": "Chicago Cubs"},
        "away": {"name": "Logan Webb",  "own_team": "San Francisco Giants"},
    },
    "best_plays": [],
}
# Possessive "Cubs' Horton" should be FINE because Horton IS on Cubs.
case5_clean = (
    "Cubs' Horton takes the hill against the Giants offense. Webb counters for "
    "the Giants with elite command. Horton's recent form drift concerns."
)

# ---- Case 6: false-positive guard — "Padres' Canning" possessive ----
case6_struct = case4_struct
case6_clean = (
    "Padres' Canning struggles against the Mets lineup. McLean projects 5.6 Ks "
    "for the Mets."
)


results = [
    case("Vazquez/Padres",  case1_bad,   case1_struct, expect_error_substr="Vazquez"),
    case("Tolle/Boston",    case2_bad,   case2_struct, expect_error_substr="Tolle"),
    case("Benge/Nationals", case3_bad,   case3_struct, expect_error_substr="Benge"),
    case("Today's Padres/Mets clean", case4_clean, case4_struct, expect_clean=True),
    case("Cubs' Horton possessive (clean)",  case5_clean, case5_struct, expect_clean=True),
    case("Padres' Canning possessive (clean)", case6_clean, case6_struct, expect_clean=True),
]

passed = sum(results)
total = len(results)
print(f"\n{passed}/{total} passed")
sys.exit(0 if passed == total else 1)
