---
name: project-playbook-reweight-821
description: 8/21 evidence-based reweight of 14 playbook signals — simulated STRONG hit rate jumps 53%→67% (beats legacy 64%).
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-21T18:17:24.103Z
---

**8/21 finding** — 30d graded playbook (n=438, base 51.1%) audit revealed weights were INVERSE of actual signal value:

**Money-losers dominating decisions:**
- `refit_conviction_strong`: 46.6% hit (-4.5pp), n=193 appearances (fires on 44% of props)
- `prop_park`: 49.5% hit (-1.6pp), n=218 appearances (fires on 50% of props)
- `pitcher_l5_confirm`: 48.6% hit (-2.6pp), n=70

**Winners underweighted / rarely firing:**
- `lineup_spot_top`: 76.2% (+25.0pp), n=21, was strength 0.30
- `opp_form_trending_wrong`: 78.6% (+27.4pp), n=14, was 0.65
- `opp_starter_weak`: 69.7% (+18.6pp), n=33, was 0.60
- `park_hitter_friendly`: 66.7% (+15.5pp), n=15, was 0.40
- `batter_l14_heat`: 64.7% (+13.6pp), n=17, was 0.70

**Reweights applied 8/21:**
| signal_key | old | new | rationale |
|---|---|---|---|
| refit_conviction_strong | (c-50)/50 | (c-50)/150 | cut 3x — was actively negative |
| prop_park | 0.55 | 0.20 | zero edge, kept as light context |
| pitcher_l5_confirm | 0.50 | 0.25 | -2.6pp signal, halved |
| lineup_spot_top | 0.30 | 0.85 | +25pp winner, boosted 2.8x |
| opp_form_trending_wrong | 0.65 | 0.85 | +27pp winner |
| opp_starter_weak | 0.60 | 0.85 | +18pp winner |
| park_hitter_friendly | 0.40 | 0.75 | +15pp winner |
| opp_bullpen_weak | 0.65 | 0.75 | +13pp winner (already relaxed) |
| batter_l14_heat | 0.70 | 0.80 | +13pp winner |
| prop_team_offense | 0.80 | 0.85 | +15pp winner |
| projection_edge_supports | edge/1.0 | edge/0.7 | +11pp winner, denominator scaled |
| projection_edge_strong | edge/1.5 | edge/1.0 | +13pp winner |
| lineup_spot_heart | 0.30 | 0.55 | +6pp modest winner |
| pitcher_last7_control | 0.50 | 0.65 | +7pp modest winner |
| batter_l7_hot | 0.60 | 0.65 | +4pp |

**Simulated impact on 30d graded rows:**
| Tier | Original | New weights | Legacy comparison |
|---|---|---|---|
| PRIME | 68.0% (25) | 67.7% (31) | 55.7% (253) — playbook +12pp |
| **STRONG** | 53.0% (83) | **66.7% (39)** | 64.1% (348) — **playbook +2.6pp** |
| LEAN | 49.4% (330) | 46.6% (148) | 52.5% (752) — filter via NO_PLAY |
| PASS | — | 49.1% (220) | — |

**Verdict:** Playbook STRONG projected to BEAT legacy STRONG for the first time. LEAN volume cut 55% — marginal props now correctly kicked to PASS. Real graded results will confirm/deny over next 3-5 days.

**How to apply:** DO NOT undo these weights without a full 7-14 day live sample. If STRONG hit rate rises above 60% on ≥30 real graded rows, ship v2.1 config permanently and greenlight Phase 3 legacy sunset. If STRONG dives below 55%, roll back to prior weights and audit which reweight overcorrected.

**Watch this closely:** L10 has minimal weight in playbook (only `player_l10_vs_line_extreme` fires — restrictive ≥8 or ≤2 condition, 44% hit on n=9). If user wants more L10 signal, add broader `player_l10_hot` / `player_l10_cold` (7+ or 3-) as follow-up work.

Related: [[project_playbook_signal_gap_819]] (parent workstream), [[project_props_pipeline_pivot]], [[feedback_confidence_in_first_pass]].
