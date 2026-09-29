---
name: project-nfl-prop-jerry-needs-work-906
description: "🚨 9/6 pre-launch: NFL Prop Jerry needs significant work before launch — user flagged during RC sandbox testing pass. Discussion queued."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T03:23:07.548Z
---

**Queued 9/6, mid-RC sandbox purchase testing:** user flagged NFL Prop Jerry "needs a lot of work" before launch. Explicit user quote: "NFL prop jerry needs work, alot of work."

**Context:** discovered while doing the paywall sandbox testing pass. User was validating the full experience and hit the NFL prop surface. Not defined what specifically is broken yet — could be:
- Prop generation quality (edge % thresholds, tier calibration)
- Prop coverage (missing player types, missing markets like pass yds / rush yds / receptions / TDs / INTs)
- Prop copy / Jerry read quality
- Book price accuracy or freshness
- Signal stack incomplete vs MLB's playbook
- Layout / UX issues on the surface itself
- Data population gaps (nfl_pipeline_props table)

**What we know is wired:**
- NFL props ship 9/2 per prior work (see [[project_nfl_phase_2_shipped_809]])
- Prop Jerry surface renders NFL when propJerrySport === 'NFL' — pulls from nfl_pipeline_props table (mirrors mlb_pipeline_props schema)
- Signal_sources plug-in port from MLB per [[project_prop_playbook_port_817]]
- Signal-gate over tier per [[feedback_signal_gate_over_tier_906]]
- Paywall preview replaces this surface for free users

**Sequence:** discussion needed BEFORE launch (per user). Not clear whether launch-blocker or fast-follow. Need to define scope + severity in the discussion.

**Related memories:**
- [[project_prop_deep_dive_tab_905]] — competing UI redesign scope
- [[project_prop_jerry_layout_v2_906]] — MLB Prop Jerry layout redesign queued
- [[project_signal_framework_821]] — cross-sport comprehensive checklist standard
- [[feedback_signal_gate_over_tier_906]] — publishability gate discipline
- [[project_nfl_ncaaf_week1_readiness_820]] — NFL/NCAAF Week 1 signal readiness

**Action item:** dedicate a discussion turn with user before launch submit to scope what specifically needs work + triage launch-blocker vs launch-day-hotfix vs post-launch.
