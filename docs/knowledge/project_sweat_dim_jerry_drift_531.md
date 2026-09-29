---
name: project-sweat-dim-jerry-drift-531
description: 5/31 sweat dim scoring fix — Jerry spread/total drivers + offense drift differential + v3 trap-zone rescue; 7 PASS games collapsed to 1
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

5/31 evening fix to `play_of_day.score_mlb_game` after user flagged the slate had too many PASS games (7 of 15) despite real edges being present (PHI/LAD with Jerry spread +6.10, NYY/OAK with confluence +9, ATL/CIN with 3.57-run xERA gap).

**What was broken:**
1. Jerry (linear deep-factor projection, shipped 5/30) wasn't read by the dim scorer at all. Only v3's `spread_delta` and `total_delta` were credited.
2. Offense drift differential (L10-vs-season R/G gap between the two lineups) wasn't scored on SIDE. PHI/LAD had a 1.89-run differential going completely uncounted.
3. v3 spread_delta 1.5-2.0 band returned zero per the 5/21 trap-zone audit, but that audit was on v3 alone. When Jerry independently confirms direction ≥2.0, the trap-zone rationale doesn't apply.

**What shipped:**
- **Jerry SIDE driver** — bands 13/8/5/2 at |jerry_signed| ≥2.0/1.5/1.0/0.5 where `jerry_signed = jerry_pred_spread + close_spread`. Same magnitude convention as v3.
- **Jerry TOTAL driver** — bands 12/9/6/3 at ≥2.5/1.5/1.0/0.5. ~30% lower than v3 because Jerry is brand new and unaudited. OVER skepticism multiplier (×0.6) applies same as v3 when v4 disagrees or v4 OVER is auto-suppressed (memory [[project_v4_over_drift]]).
- **Offense drift gap SIDE driver** — bands 8/5/3 at |home_drift - away_drift| ≥1.8/1.2/0.8.
- **v3 trap-zone rescue** — when v3_abs in [1.5, 2.0) AND Jerry confirms direction (v3_signed × jerry_signed > 0) AND jerry_abs ≥2.0, credit v3 +6 ("v3 trap-zone rescued by Jerry").

**Outcome on 5/31 slate (15 games):**
- 7 PASS → 1 PASS (KC/TEX scrubbed, KEPT at PASS only because Jerry alone shouldn't carry; all others reached LIGHT+ once the right signals were counted)
- 1 STRONG → 6 STRONG
- 0 PRIME → 1 PRIME (NYY/OAK, where the SIDE was already pipeline-endorsed via primary_play but dim wasn't crediting it on top)
- Average promotion +13 points across the slate
- PHI/LAD specifically: SIDE 44 PASS → 65 STRONG (target case validated)

**Risk to watch:**
- Jerry has 1 day of live data (since 5/30). Crediting at v3-equivalent bands on SIDE may be too generous. After 2-week audit, may need to drop Jerry side bands to 10/6/3/1.
- The fix runs the risk of letting Jerry alone push games above PASS even when v3 has zero edge. Guarded by: dim_tier requires `play` to upgrade to PRIME, and dimension only earns a `play` when actionable v3/Jerry/total directionality is established — purely Jerry-driven LIGHT_LEAN games surface no model_play and don't carry to card.

**Where:** `mlb_pipeline/play_of_day.py:score_mlb_game` around lines 524-700 (SIDE block) and 680-720 (Jerry TOTAL block).

**Related:** [[project_sweat_dimensional_redesign]], [[project_sweat_score_rewrite]], [[project_spread_delta_trap_zone]], [[project_v4_over_drift]], [[project_jerry_server_side]]
