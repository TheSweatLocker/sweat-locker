---
name: chi-59-car-37-is-a-48-5-point-total-miss-and-drives-chi-s-1-ranks
description: "One 2026 NFL result is a severe outlier that makes CHI the league's #1 offense on a 2-game sample. Unverified as of 2026-09-28 — needs an independent source before trusting or correcting."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-28T18:51:13.654Z
---

`nfl_game_results` 2026-09-13 **CHI 59 @ CAR 37 = 96 points** against a `close_total` of 47.5 — a **+48.5 overshoot**, more than double the next largest in the season (+23.5). Across 47 completed 2026 games the mean total is 46.0 and the median 41; this is the only game above 72.

**What it distorts.** With only 2 games played it drags CHI to rank **1/32 in rush yards (212.5/g), rush TDs (3.0/g) and total yards (443.0/g)** in `team_stats_rolling`. Those ranks fed the PHI @ CHI models, the Over 41.5 pick, and Jerry's "Chicago's offensive rush EPA is a sledgehammer" — i.e. one possibly-bad row propagated into a published play. CAR's `rush_yds_allowed_pg` 192.8 (32/32) is the mirror image.

**Status: UNVERIFIED, deliberately not changed.** 96 points is extreme but not impossible in the NFL, and I have no independent source wired up for 2026 results. Silently editing a score would corrupt grading receipts, so it was left alone and flagged.

**How to apply:** Verify against an independent box score before either trusting or correcting it. Separately, note the asymmetry worth closing: `line_sanity.py` validates incoming total *lines* per sport (NFL `(24.0, 70.0, 6.0)`) but **nothing validates incoming final scores**, so a bad score silently corrupts grading, `team_stats_rolling`, situational records and every model reading rolling stats. A results-side plausibility check is the structural fix. Related: [[project_margin_under_projection_926]].
