---
name: project_ats_streak_patterns_902
description: "ATS-streak / ATS-clash Vault Match patterns — TESTED AND REJECTED 2026-09-21 on 20k+ team-games, no edge at any streak length"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T04:11:41.409Z
---

❌ **CLOSED 2026-09-21 — rejected on evidence.** Approved 9/2, built and
power-tested 9/21. Recent ATS form does NOT predict the next cover.

Tested on `team_recent_games` (12,544 NCAAF / 3,434 NFL / 4,090 MLB
team-games back to 2020), streaks computed strictly from prior games.
Base rate is exactly **50.00%** by construction — every game has one
covering side, so across team-games it cannot be anything else.

    NEXT-COVER BY PRIOR STREAK      cover%   wilson95        n
    NCAAF  cold 3+                  50.29%  [47.6,52.9]   1362
    NCAAF  hot  3+                  49.28%  [46.6,51.9]   1380
    NFL    cold 3+                  50.40%  [45.4,55.4]    379
    MLB    cold 3+                  53.51%  [49.1,57.9]    484

    ATS CLASH (home 60%+ at home vs road team <=40% on road)
    NCAAF  596-598 = 49.92%  [47.1,52.7]  n=1194
    NFL    177-196 = 47.45%  [42.4,52.5]  n=373
    MLB    257-256 = 50.10%  [45.8,54.4]  n=513

Not one bucket's Wilson lower bound clears base. Patterns removed from
PATTERN_CATALOG in `92105345`.

**Why this nearly shipped anyway:** the in-catalog backtest only scores
games present in `*_game_context` (239 graded NCAAF), where
`ncaaf_ats_cold3_home_back` read 21-10-1 = **67.74% on n=31** and looked
like a clear winner. The tell was that its MIRROR — fading the HOT home
team — also beat base. Both directions of one streak cannot carry edge.

**How to apply:**
- Do not re-propose ATS streak/form patterns without new evidence.
- Before shipping ANY pattern, power-test against the deepest table
  available, not against `*_game_context` coverage. 239 games cannot
  distinguish a 68% edge from noise.
- If a pattern and its mirror both beat base, the sample is doing the
  work, not the signal. Check the mirror every time.
- The helpers survive and are lookahead-safe — reuse them:
  `_load_trg` / `_prior_games` / `_ats_streak` / `_ats_cover_pct` in
  compute_sport_patterns.py. `_prior_games` filters strictly
  `< before_date`; a form window that includes the graded game leaks the
  outcome and backtests beautifully while failing live.

Related: [[project_vault_match_901]] (parent infra, its own three bugs
fixed in `dddbf377`), [[feedback_sample_size_with_pct]].
