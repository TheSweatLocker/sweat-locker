---
name: project-nfl-ncaaf-week1-readiness-820
description: "NFL/NCAAF Week 1 readiness — pipeline + signals wired, but signal_registry empty until games grade. Weeks 1-3 will run uncalibrated."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-19T22:18:52.449Z
---

**Status as of 8/20 — NCAAF opens Fri 8/22, NFL Thu 9/4.**

**What's ready:**
- Workflows cron'd correctly (NCAAF Tue/Wed/Fri/Sat/Sun; NFL Tue-Mon coverage)
- `signal_sources` populated: NCAAF 52 signals, NFL 65 signals (incl. some prop_ signals for NFL)
- Ensemble scorer (`ensemble_scorer.py`) is sport-universal — reads sport's signal_sources rows, iterates, weights, aggregates
- Same rebuild path as MLB after 8/17 cutover — Jerry runs on top of ensemble output

**What's NOT ready:**
- **`signal_registry` is empty for both sports** — no historical fires graded
- Root cause: backfill_signal_tiers requires (ctx × results) join, but NCAAF/NFL have historical `game_results` but no historical `game_context`. Prior seasons' ctx was never persisted (either not built or rotated).
- Effect: Week 1 signals fire at DEFAULT weight (`edge_weight()` fallback), not calibrated hit rate.

**Practical implication for Weeks 1-3:**
- Ensemble emits picks BUT with less confidence separation (score distribution compressed)
- Tier bars may not fire STRONG/PRIME often on Week 1 slate
- Sharp Card should surface picks but user warning: "early-season, calibration building"
- By ~Week 3-4 (~30-50 graded games per signal), registry populates → calibrated weights kick in automatically → tier separation returns
- Same trajectory MLB went through 8/16-8/20 (post-cutover): flat weights → calibrated → useful

**How to accelerate:**
- If MLB weights can serve as PRIOR for related signals (h2h_home_dominant, etc), consider seeding NCAAF/NFL registry with MLB values × 0.5 confidence discount. Risk: sport-specific behavior differs. Only do this if user explicitly asks.
- OR: prioritize backfilling historical ctx for NCAAF/NFL. Requires re-running ctx builders for 2024-2025 games. 4-6 hours of compute.

**Related:** [[project_per_source_tracker_moat_818]] (same per-source moat mechanism will work for NFL/NCAAF once splits accumulate), [[project_playbook_signal_gap_819]] (MLB gap taught the pattern).
