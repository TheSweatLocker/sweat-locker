---
name: project-prop-playbook-port-817
description: 2026-08-17 PRE-LAUNCH port props to plug-in signal_sources playbook mirroring game architecture; needed for launch hit-rate lift
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-17T17:51:09.831Z
---

**LAUNCH BLOCKER** — port props from hardcoded scoring functions to the
same plug-in `signal_sources` architecture the game playbook uses. User
called this pre-launch on 2026-08-17: prop hit-rate lift is required
for launch, not a post-launch nice-to-have.

**Current state (2026-08-17):**
- Props scored by 12 hardcoded `score_*` functions in `generate_props.py`
  (~2500 lines): pitcher bb/ha/ks/outs/er × over/under + batter hits × over/under
- Refit weights (v2) + override gates provide discipline layer but signal
  set itself is not plug-in
- NFL/NCAAF prop pipelines are separate hardcoded generate_*_props.py files

**Target state:**
- `prop_signal_sources` table (or extend signal_sources with `subject_scope`
  column: 'game' | 'prop') storing plug-in signal definitions
- Prop-ensemble scorer analogous to game ensemble_scorer.py
- Each signal is a row with condition_expr / side_expr / strength_expr —
  add signal = INSERT row, no code deploy
- Sport-universal: MLB/NFL/NCAAF props share the same scorer, just
  different signal_sources rows

**New signal to add per user 8/17:**
Player L10-vs-line lookback (up from L5). "Did player meet the prop line
in last 10 games?" Higher weight when hit rate on this specific line is
extreme (≥80% or ≤20%). Currently signals like this are buried inside
`score_batter_hits` — surface them as a plug-in row.

**Deployment strategy per user 8/17:**
Keep legacy prop system running in parallel with new prop playbook. Only
cut over once shadow-scoring shows playbook meets or exceeds legacy
hit-rate. Same pattern as game ensemble cutover on 8/16.

**Why:** Same seed-a-row-get-a-signal flywheel that works for games.
Enables sport-universal prop pipeline (one scorer for all sports),
signal-level hit-rate tracking (today you can't say "hits_over on
L10-hot-streak is +8%" because it's buried in code), and matches the
architectural pattern user has invested in.

**How to apply:** This is real work — likely 3-5 days. Break into:
1. Design prop_signal_sources schema (subject-aware — signal fires per
   player+prop rather than per game)
2. Extract each of the 12 score_* functions into per-signal rows
3. Build prop_ensemble_scorer that aggregates per (player, prop_type,
   direction, line) tuple
4. Shadow-run in parallel with legacy for 1 week, compare hit-rates
5. Cutover when shadow beats legacy

Related: [[project-jerry-vs-sharp-card-817]] (game playbook success),
[[project-prop-edge-calibration-july]] (prior refit work),
[[project-props-pipeline-pivot]] (EV→conviction pivot history),
[[feedback-batter-hits-juice-trap-803]] (real pattern refit catches today)
