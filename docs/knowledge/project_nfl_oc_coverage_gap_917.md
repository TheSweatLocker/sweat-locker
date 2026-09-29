---
name: project-nfl-oc-coverage-gap-917
description: "NFL OC pulls succeed but only cover 1-of-16 games. compute_align_status finds 82 external rows / 0 games attached. game_id join mismatch between OC's game keys and internal nfl_game_context.game_id"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-17T23:53:07.354Z
---

**Symptom** (verified 2026-09-17 ~11pm ET):
- `pull_externals_nfl.py` runs successfully — all 9 sources return HTTP OK
- OC specifically: 3 picks / 1 game / 18 min (OC always slow)
- Dimers/Covers/Pickswise/S&O return solid coverage (15-37 picks each)
- BUT: `compute_align_status_nfl` end-step reports:
  `"0 games · 82 external rows · 0 games with S&O snapshots · 0 contexts updated"`

**Diagnosis**: 82 external picks were written to `external_picks` but the alignment step can't join them to `nfl_game_context.game_id`. Root cause almost certainly OC's game keys (URL slugs like `/games/probe-vs-probe-nfl-august-8-2026/5505028/best-odds`) not mapping to our MD5-hashed internal game_id space. Same class as [[project_nfl_game_id_mismatch_911]] (NFL/NCAAF game_id split, tuple lookup 3 sites).

**Why splits_summary still shows sources_present=['cz','fr']**: those are populated via `splits_v2_pipeline.py` reading from `fadereport_signals` + `cleatz_signals` tables. OC's raw rows land in `external_picks` (different table). The splits aggregator only reads the two source tables. To get OC into `splits_summary.sources_present`, need to also plumb OC data through the same aggregator.

**Fixes queued (v1.0.2)**:
1. Fix OC game_id resolver — map OC's team-name slugs to internal game_id via team+date lookup, same pattern used for other sources
2. Add OC data path into splits_v2_pipeline so `splits_summary.sources_present` includes 'oc' when OC has coverage for a game
3. Also fix Action Network timeout (currently 45s, may need retry or Playwright config)

**Not fixing tonight**: too deep. Money flow still works with 2 sources (cz + fr). Triple-confirmed sharp signal not firing but the base money-flow divergence signal still fires.
