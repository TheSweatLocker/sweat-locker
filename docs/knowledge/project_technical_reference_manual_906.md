---
name: project-technical-reference-manual-906
description: "🎯 9/6 queued discussion — build \"The Sweat Locker Technical Reference Manual\" as single-source doc for how everything works"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-06T12:40:46.099Z
---

**Queued for discussion later today (9/6).** User asked to create *The Sweat Locker Technical Reference Manual* — one canonical doc that captures the technical architecture, pipelines, data flows, and process rules so anyone (including future-me) can look up "how does X work" and know when/how to change it safely.

**Why:** Right now the knowledge lives in three fragmented places — memory files, code comments, and Claude conversations. A single doc reduces the cost of picking up any surface without re-deriving from the code. Also helps user spot broken invariants at a glance instead of finding them in production.

**How to apply (when we discuss):**
- Scope decision: does it cover *systems* (data flow, cron schedule, model registry, tier engine, resolver, gate logic), *processes* (how to add a sport, how to launch a new signal, how to change a tier threshold), or *both*?
- Where lives it: `docs/TECHNICAL_REFERENCE.md` in-repo (co-versioned with code), or an artifact for shareability, or Notion/GitBook for browsability?
- Update discipline: does every code change that touches a documented process require a doc update in the same PR, or is it a periodic sync? Trade-off is friction vs drift.
- Relationship to memory system: memory captures short guidance/rules; this doc captures the mechanics behind them. Memories link into the doc for the "why," doc links back to memories for the "always do this."

**Candidate sections to draft together:**
1. Pipeline map — every workflow, its schedule, its outputs, its heartbeat
2. Tier engine — the ONE canonical scoring source (post-tier-consolidation)
3. Prop composition — publishability gates, refit override, playbook gate
4. Grading + surface records — how yesterday's picks become tomorrow's headline
5. Data storage table (backend vs AsyncStorage — see [[project-consent-to-backend-906]])
6. Cross-sport registry — how to plug a new sport in without breaking the 6 wired ones
7. Model registry — where LR/refit/cohort models live, retraining cadence

**Status update 2026-09-08 (e58b2ffd):**
- **STARTED.** Lives at `docs/TECHNICAL_REFERENCE.md` (single file, co-versioned with code).
- Section format: what it does → how → where lives → how to change safely → current metrics.
- Sections done: (1) Predictive Engine, (2) LR System per-sport, (3) NCAAF LR deep dive, (4) CFBD odds backfill.
- Sections stubbed: (5) Pipeline Map, (6) Tier Engine, (7) Grading + Surface Records, (8) How to Add a New Sport.
- Every new subsystem or major change should get an entry. Prefer expanding sections over adding new ones so the doc stays scannable.
- Change log at bottom tracks edits.

**Next fill-in priorities:**
- Section 5 (Pipeline Map) — high value, low effort; documents cron schedules + concurrency groups + heartbeats
- Section 7 (Grading + Surface Records) — has the highest complexity gap between code and knowledge
- Section 6 (Tier Engine) — anchor for tier-changes questions
