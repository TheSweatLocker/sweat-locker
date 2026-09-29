---
name: feedback-validate-data-reaches-new-code
description: "When shipping code that branches on a new field, confirm the field is actually populated in the data path the new code reads from — not just that the column exists in the DB"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**When a code change adds new branches keyed on a data field, verify that the field is actually present in the in-memory data the new code reads — not just that the column exists in the DB.**

**Why:** Shipped 6/1 (commit 64629f1) split PROP dim scoring on `book_line` (PRIME ✓book = +20 vs PRIME ⚠no_book = +14). Tested the math by calling `score_mlb_game` directly with full prop rows in a smoketest — passed. Shipped to production. Beta users saw "PASS games with PRIME props inside" again 6/2 morning. Root cause: `play_of_day.run()` pre-fetched props with `select=game_id,tier,conviction` — no `book_line`. The new code was reading `p.get('book_line')` which was always None, so every prop fell through the no-book branches. The DB had the column. The smoketest passed. The integration was broken.

The pattern: shipped twice now (5/31 sweat dim fix that needed Jerry signals to actually reach the scorer; 6/1 book-line branch that needed book_line in the pre-fetch). Each time it took until the next morning when the user saw the regression.

**How to apply:**
- After editing a scorer/resolver to branch on a new field, grep for every call site that constructs the input data and confirm the field is being read/selected.
- Specifically: if the code does `p.get('new_field')`, audit every place that builds `p` — Supabase SELECTs, in-memory transforms, helper functions — to confirm `new_field` flows through.
- Run an end-to-end dry-run from a fresh process that exercises the actual production data path, not just the unit-level scoring function with a hand-crafted input.
- If the data path goes through a pre-fetch in `run()`, the SELECT clause is part of the contract — touching it must be part of any commit that adds a new branch.
- Schema validator catches missing columns in the DB. There is no equivalent "field reaches the consumer" check today. When deferred fixes mention this pattern, consider whether to ship a generic check.

**Related:** [[project_sweat_dim_jerry_drift_531]] (5/31 fix), [[project_pm_cron_live_game_prop_overwrite]] (6/1 fix family), [[feedback_always_push_after_commit]] (closely related discipline issue)
