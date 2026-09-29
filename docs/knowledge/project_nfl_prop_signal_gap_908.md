---
name: project-nfl-prop-signal-gap-908
description: "✅ RESOLVED 2026-09-15. All three fixes (A modern opp_col / B def_top10+def_bot10 signals / C weather emit) live. 9/15 slate: 44/44 non-SKIP with ≥5 signals (100% PASS). def_top10 firing on 35 rows, def_bot10 on 54 rows in latest 200-row sample."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-15T16:42:40.356Z
---

## ✅ RESOLVED 2026-09-15

Verification query on 2026-09-15 NFL slate: **44/44 non-SKIP rows carry ≥5 signals (100% PASS)**, up from the 60% baseline the original memo cited. Across the latest 200-row sample (9/13–9/15), avg signal keys = 16.9 per non-SKIP row; `def_top10` fires on 35, `def_bot10` on 54, `weather_calm` on 2, `weather_wind` on 6, `implied_high` on 24, `l5_confirm` on 65, `opp_pct` on 165 (all).

- **Fix A (modern opp_col):** shipped — sample rows show `'opp_col': 'def_pass_epa_allowed'`
- **Fix B (def_top10/def_bot10 signals):** shipped — `_emit_matchup_rank` at [nfl_generate_props.py:372](mlb_pipeline/nfl_generate_props.py#L372) is called at line 1126, producing keys on 89/165 non-SKIP rows
- **Fix C (weather):** shipped — `weather_calm/wind/cold` present on rows where conditions warrant

No further action. Original diagnosis retained below for history.

---


**Audit finding · 2026-09-07 → root-cause 2026-09-09.**

## What's populated (9/9 recheck)
- Latest NFL slate: 5 BACK / 10 PASS across 15 rows (67% PASS)
- Signal bag ships mostly 8 keys: `l4, label, opp_col, opp_pct, edge_pct, games_used, season_avg, league_baseline`
- Occasionally 17 (when L10 rows + game script fire): adds `_stat_avg_l5/l10/season, _stat_last10, implied_high, l5_confirm, _stat_games_played`

## What's DESIGNED (17 signal categories in code)
`_emit_nfl_ctx_signals` at [nfl_generate_props.py:606-786](mlb_pipeline/nfl_generate_props.py) emits: weather (wind/temp/calm), short_week, implied_high/low, game_script_run/pass, qb_vs_team, injury_load, div_game, bye_week_rest, cpoe_edge, h2h_trend, def_recent_soft/stout, target_share_elite/high/low, L10 hit-rate signals.

## Root cause of the gap (three problems)

**1. `nfl_game_context` missing fields the emitter reads:**
- `wind`, `temp`, `roof` — 0 populated (weather pull not landing in ctx despite [nfl_weather_pull.py](mlb_pipeline/nfl_weather_pull.py) existing)
- `panel_injury_outs` — 0 populated (no injury feed pipe)
- `home_qb_vs_team_recent_pass_yds_avg` / `away_qb_vs_team_...` — 0 populated (no player-H2H materialization for NFL, only MLB has this)
- **Populated and working:** close_spread, close_total, home_rest, away_rest, div_game, h2h_last5_avg_total, home_pass_cpoe, away_pass_cpoe

**2. Weak `opp_col` choice for defense percentile:**
- Pass props use `opp_col='def_pass_def'` = passes-defensed raw count from `nfl_team_stats` — noisy proxy
- `nfl_team_defense_stats` has better stats: `def_pass_epa_allowed`, `def_pass_ypg`, `def_rush_ypg`, `def_rush_epa_allowed`, `def_ppg`, `def_ypg`
- Rush props use `opp_col='def_sacks'` — sacks ≠ rush defense, wrong stat entirely

**3. `opp_pct` computed but never emitted as a why-bullet:**
- User asked "if a WR is facing a top pass defense are we taking that into account?" — answer today: computed under the hood, invisible in UI
- Need explicit signals: `def_top10_pass` (opp_pct ≤ 0.30 vs def_pass_epa_allowed) → favors UNDER, `def_bot10_pass` (opp_pct ≥ 0.70) → favors OVER, plus rush equivalents

## Fix plan (3 quick wins for Week 2)

**Fix A: Switch opp_col to modern stats**
- pass_yds/pass_tds/pass_attempts/pass_completions/reception_yds/receptions → `opp_col='def_pass_epa_allowed'` from nfl_team_defense_stats
- rush_yds/rush_tds/rush_attempts → `opp_col='def_rush_epa_allowed'`
- interceptions → `opp_col='def_ints_pg'`
- Requires loading opp_map from `nfl_team_defense_stats` instead of `nfl_team_stats`. Two-table load with fallback if defense_stats row missing.

**Fix B: Emit explicit "top/bottom-10 def" signals**
- Add rules 18/19/20 to `_emit_nfl_ctx_signals`:
  ```
  if opp_pct <= 0.30 (top-10):
      sig['def_top10'] = 'Opp {opp_team} ranks top-10 {family} defense — tough matchup'
      bonus -= 3 (over) / +3 (under)
  if opp_pct >= 0.70 (bot-10):
      sig['def_bot10'] = 'Opp {opp_team} ranks bot-10 {family} defense — soft matchup'
      bonus += 3 (over) / -3 (under)
  ```
- Requires plumbing opp_pct through to `_emit_nfl_ctx_signals` (currently computed in build_prop_row, not passed in)

**Fix C: Wire weather + injuries to nfl_game_context**
- Weather: `nfl_weather_pull.py` already exists. Verify cron schedule + UPSERT target ctx fields wind/temp/roof
- Injuries: no existing feed. Punt to later; too much surface area for Week 2

## Fix priority

- **Fix A + B this session** (1 file, both are safe refactors, huge signal boost)
- **Fix C next session** (weather wiring is small but needs cron verification)
- **NOT a launch-blocker** — the app SHOWS 100% of prop cards. Just skewing PASS.

## 2026-09-10 UPDATE — separate ctx-load bug found + fixed (03ada2c1)

While debugging user's Kittle "SIGNAL COVERAGE 0/2 gaps: target_share, game_script"
screenshot on launch day, a 4th and much bigger problem surfaced. Fix A/B still
pending, but the *ctx wasn't being loaded at all* for LLM-path props:

**Bug:** `generate_prop_jerry_synthesis.run_for_sport()` gated the ctx fetch on
`if props_for_template and _ctx_table`. When every prop went LLM path (PRIME
slates, or NFL where template-path tier-gate rarely fires), ctx_by_game stayed
empty. The LLM-path render_prop_template call that attaches structured sections
got ctx=None → coverage checklist fell back to signals-dict fuzzy match →
"target_share, game_script" flagged missing despite projected_spread + close_spread
+ home_pass_yds_pg all present in nfl_game_context.

**Fix:** load ctx whenever either lane has props (`props_for_template or props_for_llm`).

**Verified live on NFL 9/11 slate after regen:**
- Kittle Receptions Over: 0/2 → 2/2 (100%)
- Kittle Reception Yds Over: 0/3 → 3/3 (100%)
- Aggregate 46 props: ~2% → 83% coverage (34 full / 9 partial / 3 sparse)

Fix A/B (better opp_col choice + explicit top/bot-10 def signals) still on
docket — will further push toward "full" severity on remaining partial rows.

## Related
- [[project_signal_framework_821]]
- [[feedback_signal_gate_over_tier_906]]
- [[project_prop_playbook_port_817]]
- [[project_nfl_prop_jerry_needs_work_906]]
