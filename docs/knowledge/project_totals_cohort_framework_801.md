---
name: project-totals-cohort-framework-801
description: "Totals cohort attribution workstream begun 2026-08-01 — sport-universal context signals (rest, travel, venue splits, extras hangover) that determine how totals perform next game"
metadata:
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-01T18:48:49.993Z
---

Totals cohorts workstream kicked off 2026-08-01 morning after 7/31 audit showed sides had cohort attribution but totals had none (see [[project_cohort_engine_v2_workstream]]).

**User vision:** contextual situational cohorts for totals — not just current-game signals but rolling contextual state. Universal across sports. Applies to MLB, NCAAB, NFL, NBA — anywhere a total is set.

**Signal categories user surfaced:**
- Coming off the road (long road trip fatigue)
- Coming off extra innings (bullpen taxed / players tired)
- Team performance in specific stadiums (venue splits — how a team historically scores AT this park/arena)
- Universal architecture across sports

**Extended signal set to build:**
- **Rest / fatigue** — days since last game, back-to-backs, short rest starters
- **Travel** — time zones crossed, west-to-east body clock, getaway day
- **Recent context** — blowout previous (starters rested vs used) / extras used bullpen / high-scoring streak
- **Weather** (outdoor) — cold, wind out/in, humidity
- **Personnel state** — bullpen usage L3, injury replacements, pitcher rest
- **Venue splits** — team scoring at this specific venue vs season avg
- **Time-of-game** — getaway day, day-after-night
- **Umpire tendency** (MLB) — over-lean %

**Sport-specific specialties:**
- MLB: umpire, weather, pitcher rest, bullpen usage
- NCAAB: back-to-back, travel time zones, pace matchup, arena effects
- NFL: short week (Thurs), bye return, weather, dome vs outdoor conditioning
- NBA: rest days, altitude (Denver), back-to-back-to-back

**Architecture:**
- Table `totals_cohort_signals` — sport, cohort_name, direction (OVER/UNDER), lifetime pct+n, 30d, 14d, computed_at
- Signal computer per sport (`totals_signals_mlb.py`, etc.)
- Nightly backfill computes hit rates
- Wire into Jerry synthesis prompt so game-level Jerry reads for totals cite these

**Why:** 7/31 audit surfaced Jerry ML 7-6 with cohort attribution helping sides, but totals had ZERO cohort backing (only 1 total pick that night). Adding this closes the gap and unlocks Jerry's totals synthesis with the same conviction depth ML gets.

**How to apply:**
- Post-launch week 1 priority (before Props cohorts per [[project_cohort_engine_v2_workstream]])
- MVP: MLB first (data available), then NCAAB (Nov 2026 season), then NFL (Sept 2026)
- Related: [[project_dynamic_cohort_framework_607]], [[project_v2_ensemble_models]], [[project_umpire_cross_cohorts]]
