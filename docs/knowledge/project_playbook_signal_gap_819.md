---
name: project-playbook-signal-gap-819
description: Prop playbook signal port progress. 8/21: 18 legacy signals ported to signal_sources; playbook now 24 pitcher + 16 hits + 8 universal.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-21T17:47:13.034Z
---

**8/19 gap audit** — playbook trailed legacy 63% vs 81% because it iterated only 21 prop signal_sources rows while legacy fired ~60 signals per prop from `mlb_pipeline_props.signals` dict.

**8/21 port session** — added 18 signals to `signal_sources` (all `subject_scope=prop`, `sport=MLB`, backfilled via `backfill_signal_tiers.py`):

Batch 1 (pitcher stat lookups): `pitcher_xera_high`, `pitcher_xera_elite`, `pitcher_slow_start_1st_inn`, `pitcher_matchup_k_dominant`, `opp_lineup_k_heavy`

Batch 2 (recent form): `pitcher_recent_hot`, `pitcher_recent_cold`, `pitcher_recent_k_hot`, `pitcher_vs_team_dominant`, `pitcher_vs_team_owned`

Batch 3 (opponent/matchup): `opp_lineup_hot_wrc`, `opp_lineup_cold_wrc`, `opp_babip_regression_flag`, `opp_barrel_pct_hot`, `own_bullpen_gassed`

Batch 4 (walks / bullpen): `pitcher_wild_projected_bb`, `pitcher_control_projected_bb`, `own_bullpen_burned_3d`

Batch 5 (backtest-driven refinements): relaxed `opp_bullpen_weak` (dropped "soft pen" substring — legacy generic fires 72.7% n=150), relaxed `opp_form_trending_wrong` (dropped "trending wrong way" substring — 69.3% n=176), added `prop_batter_l7_warming` (l7_warm 68.5% n=108), added `prop_pitcher_l3_bad` (70.8% n=96, → outs_under BACK).

**Total after port:** 22 pitcher + 17 hits + 8 universal = 47 enabled prop signals (was 30). Plus 2 disabled based on backtest.

**Delivery 8/21:** 139/253 slate rows (55%) carry at least one new/relaxed signal. Nick Lodolo verified end-to-end: xera_high (xERA 5.99) correctly pushes ks_under + ha_over to STRONG BACK conv=77 while suppressing ks_over + ha_under to PASS conv=53.

**30d backtest against legacy graded outcomes (n=2938, base 48.6%):**
- ⭐ Big winners (KEEP AT FULL STRENGTH): wild_start 67.7% n=31 (+19.1pp), slow_start 61.2% n=147 (+12.6pp), clean_start 57.8% n=64 (+9.2pp), xera_high 56.0% n=141 (+7.4pp)
- ✗ Losers (DISABLED 8/21): `pitcher_matchup_k_dominant` (legacy whiff_elite: 43.5% n=62, -5.1pp), `own_bullpen_gassed` (legacy pen_gassed: 41.7% n=24, -6.9pp) — legacy interpretation likely inverse of mine, needs rethink before re-enable.

**High-value legacy signals still unported (Batch 6+ queue, 30d hit%):**
- opp_bullpen 72.7% n=150 (RELAXED opp_bullpen_weak partially covers)
- bvp_mastery 71.7% n=53 — batter vs pitcher career mastery (not yet emitted, needs generate_props check)
- lineup_spot 71.2% n=274 — generic, both directions
- l3_bad 70.8% n=96 (SHIPPED Batch 5)
- team_cold 70.0% n=50 (prop_team_cold covers)
- opp_starter 69.5% n=249, opp_form 69.3% n=176 (RELAXED variants Batch 5)
- l7_warm 68.5% n=108 (SHIPPED Batch 5)
- last7_walks 67.2% n=58 — L7 walks pitcher control (not yet emitted)
- l14_cooling 65.1% n=43, l14_warming 76.9% n=26
- framing 60.0% n=60 — catcher framing edge
- opp_vs_team_baa_anti 61.5% n=39

**Why:** Registry entries are populated by grading legacy prop signals (each key in `mlb_pipeline_props.signals` becomes `prop:<key>` in registry). Playbook only iterates signal_sources rows — legacy's signal firings are dark to it. So legacy sees ~60 signals per prop; playbook sees ~21. That's why playbook trailed legacy 63% vs 81% on 8/18 tiered props.

**Critical gotchas discovered during port (any future signal work must respect):**
1. `_load_prop_sources` filters on `subject_scope IN (prop, player_prop)` — MUST set `subject_scope='prop'` on every INSERT or the signal is invisible.
2. Ctx exposes pitcher name as `ctx.home_pitcher` / `ctx.away_pitcher`, NOT `ctx.home_sp_name`. `_CtxProxy.__getattr__` returns None for missing keys — silent failure.
3. `ctx.home_sp_xera` / `ctx.away_sp_xera` are the xERA columns (short prefix). But `ctx.home_first_inning_era` uses long prefix. Verify column names via a live `SELECT * FROM mlb_game_context LIMIT 1` before writing condition_expr.
4. `_matches_market` says `pitcher` scope matches `bb_/ha_/ks_/outs_/er_`. Batter signals must use `hits` or `*`.
5. Backfill script tags every prop-scoped signal `UNVALIDATED n=1` initially because grader isn't wired for prop-specific outcomes — this is expected, not a bug.
6. Full scorer run takes 4-5 min on 253 props (Windows). Use `--limit N` for iteration; full run once at the end.
7. Some ctx columns are ALL NULL on today's slate: `home_lineup_ops`, `away_lineup_ops`, `home_first_inning_bb`, `away_first_inning_bb`. Use `wrc_proxy_l14` instead of `lineup_ops`.

**Still uncovered from legacy (candidates for future batches):**
- catcher_framing_edge, pitcher_home_road_split, pitcher_l14_ip_low (durability regression)
- batter-side: batter_iso_hot, batter_vs_hand_split, bvp_matchup_history (some already exist)
- 21 VALIDATED registry signals with no corresponding signal_sources row (see 8/19 gap audit output in old memory revision)

**How to apply:** Playbook is now signal-rich enough to seriously challenge legacy on hit rate. Next: full 21d backtest to quantify win-rate lift from Batches 1-4 combined before another port sprint. If lift confirmed, proceed to Phase 3 sunset switch (kill legacy scorer writes + LLM Jerry writer).

Related: [[project_props_pipeline_pivot]], [[project_playbook_shadow_tracking_820]], [[feedback_batter_hits_juice_trap_803]].
