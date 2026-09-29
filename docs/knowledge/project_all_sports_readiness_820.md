---
name: project-all-sports-readiness-820
description: 8/20 → 8/22 CORRECTED cross-sport pipeline readiness. 5 of 7 sports on ensemble_v2 (NCAAB uses 4-lens custom resolver; UFC uses direct XGBoost predictions — ensemble wiring exists but is orphan).
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-22T18:10:39.581Z
---

**8/22 UPDATE:** 4-agent pipeline audit contradicted the original 8/20 claim of "all 7 sports on ensemble_v2 end-to-end." Only **5 of 7** actually route through `ensemble_scorer.score_game`. Two sports use different architectures — that's not necessarily broken, just NOT ensemble_v2.

**Ensemble scoring (writes primary_play):**
- ✅ MLB — via recompute_primary_play.py (cutover 8/17); legacy compute_primary_play still fires as fallback when ensemble abstains
- ✅ NFL — inline in nfl_game_context.py `_apply_ensemble` (cutover 8/16)
- ✅ NCAAF — inline in ncaaf_game_context.py:443
- ✅ NHL — inline in nhl_game_context.py:272
- ✅ NBA — inline in nba_game_context.py:207 (added 8/20)
- ❌ NCAAB — uses its own **4-lens custom resolver** (`ncaab_resolve_primary_play.py`) — KenPom / MC / Panel / Jerry vote. Does NOT call ensemble_scorer.
- ❌ UFC — uses direct 4-XGBoost predictions in `ufc_score_card.py`. Ensemble UFC wiring exists (`MARKETS_BY_SPORT['UFC']=['fight']` + ctx enrichment in ensemble_scorer.py) but nothing ever calls it. **Orphan wiring — decide: complete cutover or delete.**

**Prop Playbook status (per sport):**
- MLB: shadow-mode, latest audit (2026-08-20) says DO NOT PROMOTE — PB PRIME 68% vs legacy 80%
- NFL/NBA/NHL: table registered, shadow-capable, unlikely populated (comment in `steam_room_ladder.py:409` says "other sports don't have prop_playbook_decisions rows yet")
- NCAAF/NCAAB/UFC: **NO playbook** — not in `PROPS_TABLE` at `prop_ensemble_scorer.py:74-79`

**Tables referenced in memory notes/docs that DON'T EXIST in code:**
- `prop_playbook_shadow_results` — planned, never wired
- `prop_playbook_config` — planned, never wired
- Only `prop_playbook_decisions` is real

**Signal registry populated:**
- MLB: 132 signals, 200+ calibrated entries
- NFL: 65 signals (registry empty — populates as games grade)
- NCAAF: 52 signals (empty registry, populates Weeks 1-3)
- NHL/NBA: 40 signals each
- NCAAB: blocked on tendencies backfill (see project_ncaab_data_gap_817)
- Signal registry is ONE universal table (`signal_registry`), sport as a column — no per-sport registries. Cross-sport contamination fix landed 8/21.

**Related:** [[project_per_source_tracker_moat_818]], [[project_nfl_ncaaf_week1_readiness_820]], [[project_ncaab_data_gap_817]], [[project_ufc_model_broken_817]], [[project_pipeline_audit_822]].
