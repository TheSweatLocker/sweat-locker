---
name: v4-blackout-606
description: "6/6 morning audit found multiple silent v4 model suppressions — verify_starters schema mismatch, UFC scraper dead, v4 disagreement guard too aggressive, DAWG confluence using old ladder, log_game_result silently failing"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Shipped 6/6 morning across 4 commits (`f85c107`, `32164cc`, `b4e01cf` + the NRFI + sweat card work from earlier in the day). The morning audit surfaced an unusually deep stack of silent-failure bugs.

**v4 model silently blacked out on 4 games tonight (now 2):**
- BAL@TOR: home_pitcher=None because TOR announced Braydon Fisher but `verify_starters.py` couldn't patch the row. Schema mismatch — `_PITCHER_FIELDS` listed 10 columns that don't exist in `mlb_game_context` (sp_era, sp_hand, sp_k_pct, sp_gb_pct, sp_whiff_rate, sp_last5_era, first_inning_k/bb/hr/avg/ip, last_ip, last_pitch_count, sp_days_rest). PGRST204 error aborted the entire patch, rolling back the new pitcher name. Verify_starters had been silently failing on every starter swap since 2026-05-27.
- A's@HOU: Kade Morris (rookie) has no `mlb_pitcher_stats` row so no xERA, v4 suppressed.
- MIL@COL: COL starter still TBD at MLB API — legit blocker.
- SF@CHC: v3=5.7, v4=9.42, Jerry=9.46. The 2.5-run disagreement guard fired against v4 even though Jerry independently confirmed v4. v3 was the outlier but it got to veto v4.
- BOS@NYY: v3=7.7, v4=9.42 (delta 1.72). Should NOT have been suppressed at the 2.5 threshold — likely cron computed it with a different v3 baseline before the morning refresh dropped v3, then DB never got updated.

**Fixes shipped:**
1. `verify_starters._PITCHER_FIELDS` trimmed to only columns that exist in `mlb_game_context` (via live SELECT * inspection). Schema-mismatch silent failures fixed.
2. v4 disagreement guard threshold raised 2.5 → 4.0. Real XGBoost blind spots produce >4-run swings; 2.5-4.0 deltas are legitimate model disagreement.
3. Manual v4 backfill for SF@CHC + BOS@NYY (PATCH'd model_pred_* fields).
4. Sweat re-walk → 5 games changed tier (BOS@NYY 48→74, CLE@TEX 58→74, BAL@TOR 55→70, WSH@ARI 72→78, CIN@STL 65→76).

**UFC scraper bug (separate):** ufcstats.com put up a SHA-256 JavaScript proof-of-work challenge to block bots. Old `ufc_card_scraper.py` returns 0 tables and silently produces no event row. `ufc_picks` had been empty for 8 days. New `ufc_card_scraper_v2.py` uses ESPN's public scoreboard API which returns structured JSON. 12 fights for tonight's UFC Fight Night Muhammad vs Bonfim now ingested.

**DAWG confluence ladder (separate):** `generate_dawg_of_day.py` was still using the old "more is better" logic — `conf_mag >= 4` always granted +12 conviction regardless of magnitude. Now mirrors the SIDE dim ladder from `[[june5-cohort-audit]]`: net=4 PEAK (+12), net≥5 over-saturated (+6), net=3 edge (+6), net=2 lean (+3), net=1 slight (+1).

**log_game_result post-run audit (separate):** The 6/5 morning cron silently failed log_game_result for all 15 games. Zero rows landed. Bug only surfaced 24h later when resolver had nothing to grade and in-app recap was empty. `game_context.run()` now tracks `logged_game_ids`, post-run queries `mlb_game_results`, raises 🚨 on missing rows, AND writes minimal stub rows so the next day's resolver has something to update.

**Why:** Five distinct silent failures in one morning. Each fix is small but the pattern was the same — code printed a warning, moved on, downstream silently consumed wrong/missing data. The architectural lesson: when a fetch returns empty AND silently logs once, downstream gets poisoned for the entire 24h cycle.

**How to apply:** Tomorrow's morning audit should hit on these green: verify_starters succeeds on all starter swaps, UFC card lands via ESPN, v4 NULL count drops to 0-2 (legitimate TBD/rookie only), DAWG conviction respects the ladder, log_game_result post-run audit shows 15/15 confirmed.

Linked: [[june5-cohort-audit]] (the SIDE dim ladder that DAWG now mirrors), [[feedback-validate-data-reaches-new-code]] (PITCHER_FIELDS column mismatch is the canonical case of this).
