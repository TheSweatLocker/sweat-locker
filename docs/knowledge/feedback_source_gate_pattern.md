---
name: feedback-source-gate-pattern
description: "Stat-fetcher functions must gate sample size at the SOURCE, not at the scorer. Thin-sample data leaks into Jerry struct + social copy if you only gate downstream."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

When a stat-fetcher returns data on a thin sample (small IP / small games / call-up territory), the cleanest fix is to refuse to return it at the source. Don't store it in the DB and gate at the scorer — gate before persistence.

**Why:** 5/27 Matz incident — `get_pitcher_vs_team` only gated at `ip < 3`, returned 9.7-IP vs-BAL data showing 0.93 ERA / .200 BAA. Scorers had their own 15-IP gate (added same day) so props didn't fire on it, but the column was still in `mlb_game_context`, and the social card publicly cited it as "career mastery." Career actually was 4.23 ERA / 38.3 IP. Matz got lit 5-0 in inning 1. Brand-killer.

**How to apply:** Any external-stat fetcher (`get_pitcher_vs_team`, `get_pitcher_splits`, `get_last_3_starts`, `get_first_inning_splits`, `get_inning_bucket_splits`) needs:
1. Multi-season lookback (don't trust early-season alone — late-call-ups and rookies break thin-window assumptions)
2. Sample gate at function level — return None if undersized, never persist garbage
3. Threshold should match the consumer's confidence floor (15 IP for ERA-style claims is the current standard)

The companion `_data_quality_audit.py` script catches anything that slips past these gates: cross-checks live data against thresholds, attribution mismatches against MLB Stats API. Runs in nightly cron so the user doesn't have to catch it manually. Related: [[feedback-verify-pitcher-attribution]] [[project-pitcher-vs-team-mastery-validation]].
