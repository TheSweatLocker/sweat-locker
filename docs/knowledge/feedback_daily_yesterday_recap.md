---
name: feedback-daily-yesterday-recap
description: User wants a specific daily-recap format for yesterday's numbers — pull without being asked when the day rolls over
metadata:
  type: feedback
---

**Every morning, user wants yesterday's performance recap with these EXACT sections:**

1. **Sharp Card plays** — items on yesterday's Sharp Card + each result (W/L/P) + total units
2. **Jerry picks per game** — every jerry_reads row for yesterday MLB, one line per game: matchup + pick + tier + result
3. **Prop pipeline** — MLB props from yesterday broken down by tier (PRIME/STRONG/LEAN/COVERAGE) with W-L + hit rate + units
4. **LR assessment** — for each yesterday MLB game with `_lr_ml_shadow`, compare LR's suggested_side vs actual home_win outcome. Report LR hit rate for the day + any strong-conviction picks that landed or busted

**Why:** User uses this to gut-check the engine daily. It's a trust-verification loop — do the numbers match what the engine promised the night before.

**How to apply:**
- Offer this recap proactively when the morning audit runs or user asks "how are we looking".
- Yesterday = ET-day-before (use `datetime.now(UTC) - timedelta(hours=4) - timedelta(days=1)`).
- If prop synth still processing (early morning), note which counts may lag.
- Format: compact table per section, not paragraphs. Show units net + hit % explicitly.
- Include Rangers/underdog picks separately if noteworthy (Dawg tracking).

**Related:** [[feedback_always_include_sides_audit]], [[project_data_infrastructure_priorities_908]]
