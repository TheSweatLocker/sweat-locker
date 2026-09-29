---
name: project_bare_mask_review_920
description: "Queued for 2026-09-21 first thing - review the 51 workflow steps where `|| echo` is the ONLY error handling"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T00:46:21.575Z
---

**Next session (2026-09-21) opens with this.** Andy: "Lets do that
tomoorow first thing."

`d9b0c943` stripped 285 REDUNDANT `|| echo` masks (steps that already had
`continue-on-error: true`). Two classes were deliberately left:

- **51 steps** where the mask is the ONLY error handling — no
  `continue-on-error`. Stripping makes the job FAIL. Each needs an
  individual decision: *should* this step be able to fail the pipeline?
  This is the queued review.
- **33 masks** on continue-on-error steps with commands AFTER them.
  GitHub runs `run:` under `bash -e`, so stripping aborts the rest of
  that step — a behaviour change, not a visibility one.

Audit tooling: `strip_masks.py` / `verify_masks.py` (session scratchpad).
They classify by LOGICAL command — physical lines joined on trailing
backslash — which is what makes them safe. Re-create if gone; the naive
physical-line version corrupts multi-line curls (caught on
mlb_grade_overnight.yml:65, a 7-line curl).

**Why:** masking is why `nfl_prop_signal_discipline` ran unlocked on a
6-hourly cron all season and why the NFL lookback branch was a bare
`pass` while its step reported success every run. Silence looked like
health. Same family as [[feedback_suppression_gate_needs_shadow]] —
failures that return empty instead of erroring.

**How to apply:** for each of the 51, decide fail-loud vs
continue-on-error. Data-pull steps everything downstream depends on
(context builds, props generation, resolvers) should FAIL loudly.
Genuinely optional enrichment gets `continue-on-error: true` and loses
the mask. Do not blanket-apply either.
