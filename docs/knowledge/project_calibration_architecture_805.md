---
name: project-calibration-architecture-805
description: Architectural decision — auditable self-calibrating data layer + Jerry as analytical narrator (not decider). Ratified 2026-08-05.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-06T02:31:51.436Z
---

Cross-sport calibration framework locked in as the core architectural pattern for the prop pipeline (all sports).

**Design principle**: separate the two layers.
- **Mechanical layer** (`prop_tier_calibration.py`, `sweep_prop_coverage.py`, validators): self-calibrating from graded `prop_bucket_roi` data. Every fade/trap decision traces to specific n=X historical data. Refreshes on every module import via `_refresh_from_live_data()`. Auto-adds/removes fade combos as bucket hit rates drift.
- **Narrative layer** (Jerry synthesis): sees the calibrated context via `_calibration_fade` reason surfaced to prompt. Writes BACK/FADE prose explaining WHY the market has priced the fade in — does NOT decide direction. Improvement #11 (0c66ceae) wired this.

**Why:** Auditable > opaque. When customer asks "why did you fade Painter?" the answer is "PRIME 90+ at -115 juice hits 33% n=21 over 90d" — not "Jerry thought so." Also LLMs are bad at stats-under-uncertainty, good at narrative — use each for what they do well.

**"Detecting priced-in edge" is the differentiation vector.** Most public tools parrot projections without asking "did the book already price this in?" Our drift × juice bucket audits systematically find these traps. Nobody else has the graded-outcome loop wired this way.

**How to apply:** Every new sport gets the SAME framework. Only the signal parsers and family-name dictionaries are sport-specific. `apply_calibration()`, `_refresh_from_live_data()`, validators are all sport-generic. Don't fork the framework per sport — extend it.

**Risks to manage:**
- Small-n rules (n=12-19) shipped tonight need 60-90 days to confirm. Auto-remove handles drift back to 55%+ but audit weekly.
- Book pricing evolves; what worked in 90d can break in 30d. Refresh keeps rules current — never set-and-forget.
- Cross-sport transfer isn't automatic. Each sport discovers its own trap patterns.

Related: [[project-nfl-launch-plan-805]]
