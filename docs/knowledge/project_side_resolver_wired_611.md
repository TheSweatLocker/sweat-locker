---
name: project_side_resolver_wired_611
description: resolve_side() is now live in POTD selector. ML candidates produced by build_lean path 0b. Side gate accepts ELITE/STRONG/LEAN/LIGHT, rejects SKIP + direction-mismatch.
metadata:
  type: project
---

Commit bbc5eca (2026-06-11 morning) wired resolve_side() into play_of_day end-to-end. Two pieces:

**1. ML candidate production (build_lean path 0b)**
ML leans were removed 5/1 pending projection_v2. Path 0b now produces them from resolve_side STRONG/ELITE. Direction maps to home/away team ML. Juiced chalk (-180 or worse) defers to the existing _rl_alt_for_juiced_chalk path. lean_display format: `{team} ML (resolver STRONG)`.

**2. SIDE RESOLVER GATE in run()** (parallel to existing total gate)
Accepts ELITE/STRONG/LEAN/LIGHT, rejects SKIP and direction-mismatch contests. Permissive vs the total gate (which requires STRONG+) because the side audit showed LIGHT was +26.9% ROI vs total LIGHT's -15.7%.

**Tier ordering for audit_pool sort:**
`_resolver_rank = {'ELITE': 0, 'STRONG': 1, 'LEAN': 2, 'LIGHT': 3}` (untiered = 4). STRONG total beats LIGHT ML beats untiered.

**Day 1 live observation (6/11):** ARI @ MIA Marlins ML surfaced as POTD runner-up (didn't exist as a candidate before — no individual model crossed v2/v4 thresholds). STL @ NYM Over 9.0 (total STRONG) still won on higher audit rate.

**Why:** Completes the unified resolver framework. Total resolver shipped 6/10; side was the missing dimension. ML/RL POTD selection now uses the same baseline-normalized + cross-signal landing call instead of confluence_prime_ge4 / autofade_dog_high_conv cohorts alone (those still gate, but the resolver provides the loud signal).

**How to apply:** When debugging "why didn't game X get picked," check both gates — total gate requires STRONG+, side gate requires non-SKIP + direction match. _resolver_tier stamp on candidate dict reveals which fired. Future architectural shift: replace cohort_prime_ge4 audit with resolver tier audit rates directly (deferred — needs forward-test sample).

Related: [[project_re_weight_model_votes_609]] [[project_potd_audit_queued_607]] [[feedback_let_engine_speak]]
