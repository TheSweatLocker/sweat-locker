---
name: feedback-signal-gate-over-tier-906
description: "Every prop composer must filter on signal-quality gates (_coverage_kill_gate, _playbook_prop_gate, _lr_tier_raw) — never on tier field alone"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-06T13:09:54.911Z
---

Every prop composer (sharp card, prop card, POTD, ladder, etc.) MUST call `_is_prop_publishable(prop)` before including a prop, regardless of its `tier` field. Tier can lag reality because it's set at prop generation and demoted later by `apply_refit_verdict_override.py`. Composing off `tier` alone lets the composer publish props the pipeline already flagged as unpublishable.

**Why:** 9/5 Sharp Card audit found 8 props labeled PRIME that the pipeline had flagged internally as `_playbook_prop_gate='NO_VALIDATED_SIGNALS'`. All 8 lost. Counterfactual replay with the gate turned on: 17→16 items, 7-8→12-4, -6u→+13.77u (+43% ROI swing). Same class of bug will hit NFL/NCAAF the moment those props wire onto the card. See [[project-app-store-submission-901]] launch scrub.

**How to apply:**
- `is_publishable(prop)` (from `mlb_pipeline/prop_publishability.py`) checks: `signals._coverage_kill_gate` (kill), and LR-vs-stored tier drift (kill).
- `effective_tier(prop)` picks conservative of `prop.tier` and `signals._lr_tier_raw` so "PRIME" on a user surface always means LR-verified PRIME
- **`_playbook_prop_gate` is DIAGNOSTIC, NOT a kill.** Yesterday-audit (9/5) found 16 of 22 winning PRIMEs had it set — blocking on it throws out winners. LR outperforms the playbook signal registry validator when LR-tier is high. Do NOT re-add it as a gate. See `_DIAGNOSTIC_PLAYBOOK_GATES` constant in the module for the paper trail.
- Any prop composer added later — for any sport, any surface — must call both. `nfl_pipeline_props` / `ncaaf_pipeline_props` share the same `signals` schema so the helpers are universal.
- When adding a new composer, log dropped-by-gate counters (composer prints them) so silent regressions surface immediately if a pipeline change floods a gate.

**Related:** [[feedback-backside-dictates-app-renders]] (data decisions server-side), [[feedback-validate-data-reaches-new-code]] (verify at every call site).
