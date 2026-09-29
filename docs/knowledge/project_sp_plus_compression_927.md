---
name: project_sp_plus_compression_927
description: "NCAAF SP+ predicted points are compressed ~1.7x vs market and actual, which IS the dog bias; raw sp_overall diff is correctly scaled"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-27T21:59:11.624Z
---

Measured 2026-09-27 on 156 leak-controlled NCAAF 2026 games.

**ncaaf_game_context is a VALID backtest surface.** 414 of 421 season-2026
rows have `computed_at` BEFORE `game_date` (6 same-day, 1 after). This
REFINES [[project_sp_plus_backtests_are_leaky_926]]: the leak applies to
querying `sp_overall` from the stats table today, NOT to the per-game
snapshot columns on the context row. Rows still need filtering on
`team_form_enriched_at < kickoff_utc` — that field is same-day on 327 rows
and the Saturday pipeline runs ~17:45 ET, after noon kickoffs.

**Raw SP+ differential is correctly scaled.**
  market_margin = +4.28 + 0.976 * (home_sp_overall - away_sp_overall)
  actual_margin = +4.96 + 0.973 * (home_sp_overall - away_sp_overall)
Slope ~0.97 against both. So sp_overall diff + ~4.5 HFA is already right.

**The pipeline's own SP+ points projection is compressed ~1.7-1.8x.**
  market_margin = +0.18 + 1.663 * sp_plus_pred_margin   (n=75)
  actual_margin = -0.38 + 1.836 * sp_plus_pred_margin
  |sp_pred| < |market| in 61/75 (81%), mean -5.52 pts
Source is the averaging at ncaaf_game_context.py:784 —
`h_pts = (h_sp_off + a_sp_def)/2` pulls both teams toward the middle.
NOT a clean /2 of sp_overall diff (median ratio 0.855, p10 -0.21 p90 1.67),
so do not describe it as "exactly half".

**This compression IS the dog bias** ([[project_ncaaf_dog_bias_926]],
[[project_margin_under_projection_926]]). A compressed margin always makes
the favourite look over-priced. Share of SP+-derived edges pointing at the
DOG, by scaling slope: 0.5 -> 96%, 1.0 (today) -> 75%, 1.5 -> 47%.

**Fixing the scale removes the bias but creates NO edge.** ATS is
52.7-53.6% at EVERY slope (n~150) against a 52.4% breakeven. And the
market predicts margin better than SP+ does: MAE 10.43 (market) vs 12.31
(sp_pred) on the same 75 games. So SP+ must never override the market on
magnitude — it is a confirmation lens, not an edge source.

**Rolling EPA is NOT opponent-adjusted, and that is most of its divergence
from SP+.** Correlation of the two percentile views is 0.792 — largely the
same opinion, so treating them as independent lenses double-counts (same
trap already documented for sp_plus_pred_total). Divergence is a schedule
artifact in both directions:
  rolling HOT  = soft schedule: Troy (avg opp SP+ -9.3), Jacksonville St
                 (-8.4), Kennesaw (-4.6), North Dakota State (FCS, -3.6)
  rolling COLD = brutal schedule: Wisconsin (+9.1), Texas (+8.9),
                 Purdue (+8.0), Illinois (+3.1)
Raw EPA therefore systematically backs soft schedules and fades hard ones.
Its 58.8% ATS (n=80) is SUSPECT until opponent-adjusted.

**Coverage gaps found:** `sp_plus_pred_home_pts` missing on 173 of 421
games (41%). `sor` is NULL for every team. 143 of 261 teams have both SP+
and rolling EPA.

**Tuning order recommended:** (1) opponent-adjust rolling EPA — the real
prize, makes rolling additive instead of a schedule proxy; (2) fix the
SP+->points scale to kill the dog bias; (3) stop counting SP+ and rolling
EPA as independent lenses at r=0.79; (4) never let SP+ override market on
magnitude.
