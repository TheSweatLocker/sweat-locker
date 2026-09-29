---
name: test-sports-not-scope-limit
description: "When user names sports for \"testing,\" treat as canary set — not scope limit. Build cross-sport, use named sports as verification target."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-01T22:49:03.673Z
---

When the user names one or two live sports as the "test" targets for a cross-sport feature, that's the canary set (currently live, easy to verify) — NOT the scope limit. Build the feature for ALL current + upcoming sports at once. New sport onboarding = zero engineering work.

**Why:** User callout 2026-09-01 after I shipped Tier 1 rollup matviews with only MLB + NCAAF UNION blocks when NFL/NBA/NCAAB/NHL were coming online in Sept-Nov. Their exact words: "Shouldn't we have done this across all sports, not just NCAAF/MLB, I only mentioned those because those were the only live sports right now?" Their earlier "test on MLB/NCAAF next couple days" meant "use these two live sports to verify the pattern," not "only support these two." Scoping out other sports violates the [[project_rolling_rollup_architecture_901]] uniformity principle.

**How to apply:**
- When user names sport(s) for testing, include ALL 6 sports (MLB, NFL, NBA, NCAAB, NCAAF, NHL, UFC when applicable) in the initial ship. Even if some sports have no source data yet — the UNION block returns 0 rows gracefully and populates automatically when the source data lands.
- Frame verification, not scope: "shipping cross-sport, using MLB/NCAAF for the couple-day verification since they're live" — never "scoping this to MLB/NCAAF."
- If a sport's source schema doesn't fit yet (missing tables), still commit to it by adding a stub / TODO comment in the migration UNION so it's obvious the block needs completion, rather than silently omitting.
- Related: [[project_rolling_rollup_architecture_901]], [[feedback_universal_vs_sport_specific]] (default universal), [[feedback_data_visibility_over_layout]].
