---
name: everything-means-all-lenses
description: "When user says \"everything\" for a breakdown, include EVERY lens — panels, cohorts, composites, models, numbers, externals, MC, projections. No exceptions."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-22T23:09:31.295Z
---

When user asks for "full stat projections" or "everything" or "all lenses" or
"the whole nine" for a game breakdown, the answer MUST include every layer:

- **v3** (projected_spread/total — composite baseline)
- **v4** (model_pred — XGBoost)
- **Jerry** (LLM prediction spread/total + team runs)
- **Composite** (weighted_composite_spread output)
- **Panel** (panel_implied_margin + panel_implied_total — compute on-the-fly
  from pitcher projections if not in game_context yet)
- **Confluence** (net + breakdown + home_lean)
- **MC probabilities** (win/cover/over/under with 10k sims)
- **Cohorts** (cohort_tags + hit rates from mlb_tier_calibration)
- **Primary play** (structured play + tier + audit note)
- **NRFI/YRFI** (score + 1st-inn ERAs)
- **Pitcher projections** (outs, ER, Ks, hits, BB)
- **Offense** (wRC+, OPS L7/L14/season, vs opp-hand)
- **Bullpen** (ERA + 3d relievers)
- **Line movement** (open→close→current for spread/total/ML)
- **Weather/park** (temp, wind, PRF, roof)
- **External picks** (per-source, with fade_flag, surface, confidence)

**Why:** 7/22 user asked for "all lenses" on evening slate and I omitted
Panel. User called it out. Panel wasn't in game_context yet (migration
pending), but the inputs were there and I should have computed on-the-fly.

**How to apply:** Before returning any "full breakdown" or "everything"
response, checklist the list above. If ANY lens is missing, either compute
it or explicitly note the reason it's absent. Never silently omit.

Related: [[feedback_backside_dictates_app_renders]], [[project_audit_721_full]]
