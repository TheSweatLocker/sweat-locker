---
name: may29-docket
description: "5/29 docket additions — book-line recalibration for HA/ER/Outs/BB, sweat-score surfacing redesign, baseball model totals/projections discussion"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Three items added 2026-05-29 alongside the existing backside queue:

**1. Phase 2 Odds API expansion — book lines for HA / ER / Outs / BB**
- Same calibration pattern as the K prop recalibration shipped earlier in May. Tonight's 5/29 audit proved the gap: Meyer U5.5 PRIME 91 → U4.5 STRONG, Lorenzen O2.5 PRIME 86 → O3.5 +110 LEAN, Rodón U5.5 PRIME 80 → probable SKIP at U4.5.
- Until this ships, every non-K pitcher prop risks publishing convictions calibrated to internal `suggested_line` that doesn't match the book. Shadows the trust-killing trap we fixed for K's.

**Why:** Internal `suggested_line` is fine for a starting heuristic but the conviction tier must reflect edge vs the *actual* book the user can bet. We have the recipe — `fetch_book_lines_for_ks()` + `recalibrate_k_props_with_book_lines()` + edge-band multipliers (0.30/0.55/0.75/0.90/1.00) — port to the other pitcher prop types.

**How to apply:** When pulling this off the queue, mirror `mlb_pipeline/generate_props.py` K-recal architecture: per-prop-type `fetch_book_lines_*`, `attach_book_lines`, `recalibrate_*_props_with_book_lines`, retain SKIP'd rows with `_pre_recal_*` trace fields for backtest. Don't skip the LEAN-promotion gate on positive-edge minimal-conviction rows.

**2. Sweat score surfacing audit — does it tell the user what play the model likes?**
- Current state: `sweat_score` is a side-of-game number (0-100, tiered 80/65/50/<50 PRIME/STRONG/LIGHT_LEAN/PASS). Total-only edges disappear: ATL/CIN tonight sweat 38 PASS despite model_total +0.93, four PRIME/STRONG props all aligned on the same total Over.
- Open questions to work through:
  - Split into `side_sweat` / `total_sweat` / `prop_sweat`?
  - Add `model_likes` field on the card (e.g. "Total Over", "Yankees ML", "NRFI") so the user sees the *play*, not just the heat number?
  - Re-audit the 80/65/50/<50 bin thresholds post-K-recal — has the distribution shifted?

**Why:** Tonight's reader confusion ("half the games are PASS") is partly a real slate-tightness symptom and partly a *surfacing* problem — sweat tier under-weights total-only opportunities, so games with real model edge get filtered out of the card by tier alone.

**How to apply:** When the user wants to dig in, start by enumerating what "PASS" tonight would have if we had a parallel `total_sweat` — would ATL/CIN at 38 side-sweat become STRONG total-sweat? Would publishing the per-play decomposition change the card?

**3. Baseball model discussion: totals + projections**
- Open discussion session — not a single ticket. Topics worth sequencing when the user picks this up:
  - v4 XGBoost OVER-side calibration drift (43% 30d / 41% 7d / 25% 3d on OVERs). UNDER side healthy. Are we suppressing real OVER edges? `project_v4_over_drift` has the suppression-gate context.
  - Per-starter IP/ER/Outs class projections vs wRC+-bucketed offenses — Phase B from `project_pitcher_class_projections`. Phase A (scout report integration) shipped; Phase B (prop scoring) queued for v1.1.
  - v1.1 FanGraphs L7/L14 wRC+ — currently a drift proxy; pull true rolling. `project_v11_recency_wrc`.
  - Recency-weighted v5 retrain timing (post-launch once sample is sufficient).

Related: [[v4-over-drift]], [[pitcher-class-projections]], [[v11-recency-wrc]], [[k-over-audit-cohorts]] (the K recal recipe).
