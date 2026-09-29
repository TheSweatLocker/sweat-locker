---
name: feedback-fix-at-root-three-parts
description: "A data bug isn't fixed until write path + existing data + DB constraint are all done. Patching only the writer is the recurring failure mode."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-18T21:49:29.093Z
---

**A data-integrity fix is not done until all three parts ship:
(1) the write path, (2) cleanup of rows the bug already wrote,
(3) a DB-level constraint so no future writer can repeat it.**

**Why:** Andy, 2026-09-18: *"fix the issues found at root so doesnt
happen anymore"* and separately *"we need to fix composer and not just
add more shit all crazy like you have been doing."*

The NCAAF game_id/date drift is the canonical example. On 2026-09-16
the write path in `ncaaf_odds_pull.py` was correctly patched to use the
ET date. Two days later that bug was still actively corrupting output:
52 orphan rows from the old path were never cleaned up, no constraint
existed, and the Sharp Card published Oregon -56.5 STRONG/77 from a
stale 9/15 row while the live row said -58.5 LEAN/62. The patch fixed
one writer and nothing else, so the damage kept flowing.

**How to apply:**

1. **Write path** — fix the code that produces bad data. Necessary,
   never sufficient.
2. **Existing data** — the bad rows are still there and still being
   read. Write a repair script with `--dry-run` default. Verify
   downstream references BEFORE deleting anything (see gotcha below).
3. **Constraint** — a CHECK or UNIQUE at the DB. This is the part that
   makes it stay fixed, because it binds every writer, including ones
   that don't exist yet. Let the migration FAIL on violating rows
   rather than going `NOT VALID` — a failure means step 2 is
   incomplete, which is information.

**Do NOT "fix" it by adding a filter downstream** (a dedup pass in the
composer, another gate in the scorer). That is the compensating layer
Andy keeps rejecting. It leaves the bad data in place, adds a code path
to maintain, and hides the problem from the next person.

**Gotcha — verify downstream refs with a real query.** On 2026-09-18 a
PostgREST `in.(...)` filter over game_ids containing spaces silently
returned 0 matches, which read as "safe to delete." The true answer was
51 of 52 orphans were referenced by `jerry_reads`. A blind delete would
have orphaned 51 reads. Cross-check any "0 references" result against a
full-table pull and set intersection before trusting it.

**Also preserve freshness when deduping:** the row with the *correct*
id is not always the row with the *newest* data. Copy the newer
payload onto the survivor before deleting the loser.

Related: [[feedback-source-gate-pattern]],
[[feedback-validate-data-reaches-new-code]],
[[feedback-publishable-view-drift]],
[[project-ncaaf-ingest-duplicate-902]]
