---
name: system-integrity-sweep-725
description: "Full silent-default DQ sweep across 24 critical tables on 7/25 after pitcher_stats fix. Found ONE other issue (nba_team_stats orphan defaults, now NULLed). Confirmed pitcher_stats bug was isolated, not systemic."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-25T22:58:36.077Z
---

**Set 2026-07-25 after full system-integrity sweep triggered by the
pitcher_stats hardcoded-defaults finding.**

## Motivation

Pitcher_stats issue (78% of rows stuck at whiff=10.0 / hard=35.0 /
barrel=6.0 / velo=93.0 / lob=72.0 due to `safe_float(value, DEFAULT)`)
prompted an audit — if it happened once, likely happened elsewhere.
Andy called it: "system integrity is vital."

## Sweep approach

Script: `mlb_pipeline/_system_integrity_sweep.py`. For each critical
model-feed table, pull all rows + count numeric column distributions.
Flag columns where a single "round" value holds ≥30% share with ≥50
rows. Cross-check code to distinguish real bugs from legit constants.

## Findings

### FALSE ALARMS (legitimate constants)
- `mlb_team_offense.last5/10/20_games_sampled = 5/10/20` — sample-size markers.
- `prop_edge_calibration.window_days = 30` — audit window constant.
- `nfl_team_stats.games = 17` — full NFL season length.
- `mlb_team_vs_opp_recent.games_played = 3` — series length.
- `mlb_bullpen_stats.pitching_*_ip` — table misnamed; actually full-team
  pitching bucketed per inning phase, 309/309/297 IP are legit season totals.

### DEAD-CODE ORPHANS (nulled 7/25)
- `nba_team_stats.efg_pct_defense = 52.0` on 30/30 teams
- `nba_team_stats.ft_rate = 25.0` on 30/30 teams

Neither column written or read by any Python code. Zero impact on live
picks. NULLed to prevent future false-signal risk. Followup: DROP
migration in October before NBA season starts.

### ACTIVE BUGS FOUND
**Zero.**

## Interpretation

The pitcher_stats issue was isolated, not systemic. The `safe_float(v,
HARDCODED_DEFAULT)` anti-pattern doesn't appear elsewhere in data
writers. 24 tables audited, all clean.

## Standing practice

Run `_system_integrity_sweep.py` monthly + after any new stats-writer
ships. <30s runtime, catches this class of bug before it corrupts a
model lens.

## Related

- [[project_miller_k_prop_postmortem_725]] — trigger event
- [[feedback_source_gate_pattern]] — gate at source, not scorer
