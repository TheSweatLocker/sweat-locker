---
name: project_sp_plus_backtests_are_leaky_926
description: "Any SP+ backtest before 2026-09-26 is leaky — ncaaf_team_stats.sp_overall is current season-to-date, so it contains the games being predicted"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-26T21:31:13.159Z
---

`ncaaf_team_stats.sp_overall` holds the CURRENT season-to-date rating, not
an as-of-date value. Regressing past game margins or closing spreads on it
leaks: a team that already won big has an inflated rating, so the fitted
coefficient is biased UPWARD and the model looks more accurate than it is.

Measured 2026-09-26 while investigating the NCAAF dog bias, and every number
below is contaminated — recorded so they are not quoted as findings:
- "SP+ projection MAE 9.22 vs market 10.88" — artifact, not a real edge
- "true K_PTS_SP is 0.979 (margins) / 0.931 (market) vs shipped 0.85" —
  biased high by the leak, so **0.85 may well be correct. Do not change it
  on this evidence.**

`team_stats_rolling_history` (migration 20260926a) starts accumulating
as-of-date snapshots on 2026-09-26 and its own header says the prior history
"is not recoverable." So SP+ becomes backtestable roughly 6-8 weeks later,
not before. Until then, treat any SP+-derived calibration claim as unproven.

What survived the leak check and IS solid, because none of it touches SP+:
- NCAAF picks taking the dog 8-22 (26.7%) vs favourite 50-40 (55.6%),
  grading audited 104 agree / 0 disagree
- dogs cover ~50% in every spread band historically (n=6,188), so the market
  is efficient and our selection is the broken part — see [[project_ncaaf_dog_bias_926]]
- FBS-vs-FCS games have no SP+ on one side, fall to the EPA branch, and
  produce ~1-5 pt margins against 30-45 pt markets — fixed at source in
  ncaaf_game_context.compute_primary_play

Related: [[project_prop_l5_leak_922]] (same leak class, props),
[[feedback_suppression_gate_needs_shadow]].
