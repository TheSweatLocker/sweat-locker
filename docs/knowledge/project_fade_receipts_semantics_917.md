---
name: fade-receipts-semantics-917
description: "Fade direction-flip receipts grading verified correct 2026-09-17 — grader tracks fade TARGET (backed side) consistently across seeded and runtime fade paths."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-17T16:07:19.560Z
---

**Verified 2026-09-17.** Backlog item "direction-flip receipts semantics
(grader tracks fade TARGET not backing side)" reviewed end-to-end. No bug —
semantics are consistent across three paths:

1. **Seeded fade signals** (`discover_patterns._seed_findings` lines 611-628):
   `_fade` suffix on signal_key. `side_expr` = flipped target
   ({over→UNDER, HOME→AWAY_ML, etc.}). `hit_rate_pct` = `100 - raw` (target
   hit rate, not signal-fired rate). Verified: 32 MLB `_fade` rows in
   `signal_sources`, every one's `side_expr` points to what we back.

2. **Runtime fade opinions** (`ensemble_scorer` lines 1136-1176):
   ANTI_VALIDATED tier + hr≤0.47 triggers auto-fade. Emits Opinion with
   `side=flipped_side` (target) and `signal_key=<parent>__fade` (double
   underscore).

3. **Grader** (`refit_signal_registry.collect_prop_firings:110-134` +
   `collect_game_firings:216-241`): strips `__fade` for parent grouping,
   compares `src_side` (which is the target for fades) to the market
   winner. A fade "backs UNDER" opinion is correct iff UNDER hits.

**Why:** The parenthetical "grader tracks fade TARGET not backing side"
could be read either as diagnosis or as bug — verified as diagnosis. Target
IS what we back; the tautology is intentional.

**How to apply:** If a future fade-vs-outcome divergence is claimed,
verify against these code paths first. The single-underscore `_fade`
suffix (seeded) is treated as its own signal_key; the double-underscore
`__fade` (runtime) rolls up to parent for calibration.
