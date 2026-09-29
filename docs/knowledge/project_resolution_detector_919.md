---
name: project-resolution-detector-919
description: reconcile_resolution.py is the cross-sport resolution detector. Run it before trusting any record or claiming grading is healthy.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-19T07:16:44.551Z
---

**`mlb_pipeline/reconcile_resolution.py` (commit 2f9bbf2f) is the
detector for resolution bugs. Run it before trusting any record,
publishing a stat, or telling Andy grading is fine.**

    python mlb_pipeline/reconcile_resolution.py --days 7
    python mlb_pipeline/reconcile_resolution.py --sport NFL --quiet

Read-only, writes nothing, exits 1 on CRITICAL so a pipeline step can
gate on it.

**Why:** On 2026-09-18 five separate resolution bugs surfaced in one
day — NFL props graded off `final_value=0.0`, MLB top_8 picks stuck
Pending on prop_line drift, POTD hardcoded to `sport=MLB`, NCAAF
duplicate contexts disagreeing on the pick, two NCAAF games final with
nothing graded. **Every one was found because a human noticed.** Andy:
*"We need to figure out resolving across sports this is weak area."*
The weakness was never the code quality — it was that nothing checked.

**Checks:** STALE (past-date picks with no result) · ZEROVAL (graded
against an impossible value) · DANGLING (ungraded card picks whose
source row is gone) · ORPHANDATE (props on a date with no games) ·
DUPCTX (one matchup, multiple context rows).

**How to apply — the tuning lessons matter more than the script:**

A detector that cries wolf gets ignored. Three passes were needed
before the output was trustworthy, and every false positive came from
me not knowing the domain:

- **Read the publishable view, not the raw table.** Counting raw rows
  reported 866 stale MLB props; the real number was 69. Banned
  families never publish, so their grade is irrelevant. "What a user
  could see" is the only denominator that means anything — same
  lesson as [[feedback-surface-records-trust-levels]].
- **Zero is usually REAL.** Flagging `final_value=0.0` reported 63.7%
  of MLB props, but `hr_over` was 148/163 zeros on 9/17 and all were
  correct — most batters don't homer. Zero is only suspicious where
  it is implausible *for a player who appeared*: pitcher outs, pass
  attempts, pass completions. Never batter counting stats.
- **`Void` and `UNGRADEABLE` are correct behaviour, not defects.**
  505 MLB + 35 NFL rows had a NULL value because the resolver
  correctly refused to grade a DNP. Only Win/Loss/Push with no value
  is a real defect.
- **A dead reference on an already-graded pick is history.** Only an
  *ungraded* pick with a dangling source is actionable.

**Verification it works:** NCAAF DUPCTX read 13 matchups (5 with
conflicting picks) before the gid-drift repair and 0 after — the
detector confirming a fix landed rather than anyone eyeballing it.

Not yet wired to a workflow. Doing so is the remaining step, ideally
post-slate so failures surface the same night.

Related: [[feedback-fix-at-root-three-parts]],
[[feedback-grading-zero-fail-912]],
[[feedback-surface-records-trust-levels]],
[[project-ncaaf-ingest-duplicate-902]]
