---
name: project-scoresandodds-sprint-824
description: ScoresAndOdds wire-up + splits schema v2 refactor — user-approved 2-day sprint queued for 2026-08-24
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-23T18:51:45.671Z
---

**Decision (2026-08-23):** ScoresAndOdds gets built via the FULL proposal, not the fast-path shortcut. Full sprint queued for the day after (2026-08-24) or next available focused session before NCAAB Nov 3 launch.

**Why:** User picked "Proper: do the full 2-day sprint per proposal" over the shortcut ("Fast: build scraper against existing schema, ~2 hrs"). Reasoning: cleaner arch matters more than tonight's ship, especially because Phase 3-5 also extends Fadereport + Cleatz cross-sport (which shortcut wouldn't do) and sets up NCAAB Nov 3 with a source-agnostic splits pipeline.

**How to apply:** When the sprint starts, work from `docs/splits_schema_v2_proposal.md` in order — Phase 1 (schema v2 + backfill) MUST land before Phase 2 (SO scraper). Every scraper writes to `public_splits_v2` uniformly, not source-specific tables. App cutover (Phase 4) reads only `game_context.splits_summary` JSONB.

**Sprint budget (per proposal):**
- Phase 1 schema + backfill: ~2h
- Phase 2 SO scraper 6 sports: ~3h  
- Phase 3 extend FR/CZ cross-sport: ~4h
- Phase 4 app cutover: ~1h
- Phase 5 externals cross-sport: ~2h per sport
- **Total: ~14-18h, planned as 2 focused days**

**Current split source coverage (as of 2026-08-23):**
- OddsCrowd — live all major sports
- Fadereport — live MLB only (Phase 3 target: extend to NFL/NCAAF/NBA/NHL/NCAAB)
- Cleatz — live MLB/NFL/CFB (Phase 3 target: verify NBA/NHL/NCAAB)
- ScoresAndOdds — 🚫 not built (Phase 2 target)

After sprint: every sport has ≥3 sources for triple-confirm signals.

Related: [[project-pipeline-audit-822]], [[project-dissent-audit-822]]
