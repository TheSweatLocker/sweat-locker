---
name: project_nba_ncaab_discussion_queue_911
description: "2026-09-12 morning discussion queue — NBA modeling architecture + products + prop jerry direction, then NCAAB product scope before Nov 3 launch. Post-launch morning-after review agenda."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-12T03:39:38.296Z
---

# NBA + NCAAB discussion queue — 2026-09-12 morning

Queued by Andy 2026-09-11 night as the app crossed launch day. To
walk through the morning after the first real Sunday of publishable
NFL picks lands.

## NBA — three tracks

1. **Modeling.** NBA opens 10/22. Currently Elo-only ([[project_elo_models_shipped_817]])
   with the ESPN rebuild landed 8/17 ([[project_nba_rebuild_status_817]]).
   Need to walk through: what layers we run before opening night —
   does NBA get the same 4-model ensemble (panel + LR shadow + MC
   dissent + cohort match) or a leaner version to start? Cohort
   framework was written universal ([[project_cohort_engine_universal_architecture]])
   but NBA rows in `cohort_stats.json` are stub. Decide: launch NBA
   opening with 2-lens (Elo + market fade) or wait to layer.

2. **Products.** Currently NBA has sides + totals but no props. Do
   we ship NBA props at opening or hold to Dec? Reference:
   [[project_ncaab_v4_deferred_814]] (parallel decision for NCAAB
   V4 target Dec 3). Similar shape for NBA — 2-lens at open, V4
   promoted mid-season once we've graded 10+ nights.

3. **Prop Jerry.** [[project_prop_jerry_layout_v2_906]] +
   [[project_nfl_prop_jerry_needs_work_906]] show the current
   discipline framework is Prop Jerry v2 (structured template
   render, not free-form LLM). NBA opening should inherit v2
   directly. Discuss: what NBA-specific signals go into the
   template (pace, matchup rank, injury-driven usage bumps).

## NCAAB — pre-launch scope

- Nov 3 launch date fixed ([[project_ncaab_v4_deferred_814]]).
- Data gap flagged: [[project_ncaab_data_gap_817]] says 0/5911 games
  have spread/total. Discuss where we pull that from before 11/3.
- Product scope: 4-lens ensemble at launch, V4 deferred to 12/3.
- No props ([[feedback_college_sports_no_props]] — hard rule for
  college sports across the app).

## Order of discussion (suggested)

1. NBA modeling architecture decision — 2-lens vs 4-model at open
2. NBA products — sides+totals only vs sides+totals+props
3. NBA prop Jerry — signal set + template contents
4. NCAAB — data pull for spread/total (blocker)
5. NCAAB — 4-lens tuning + product scope

## Related pending discussions

- [[project_picks_engine_walkthrough_908]] — cross-sport pick engine
  walkthrough queued
- [[project_selection_grading_walkthrough_909]] — cross-sport
  selection+grading walkthrough queued
- [[project_scale_1500_users_911]] — capacity plan queued if launch
  traffic warrants it
