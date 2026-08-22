# mlb_pipeline.yml Split Proposal (Batch 3c)

**Status:** Proposal — NOT activated. Review before implementation session.
**Date:** 2026-08-22
**Context:** [project_pipeline_audit_822](../.claude/projects/c--Users-gomez-SweatShop/memory/project_pipeline_audit_822.md)

## Problem

`mlb_pipeline.yml` currently has **135 steps** and fires **twice daily** (6am + 2pm ET). Every cron cycle runs the entire chain — including work that only makes sense once daily (game reads), weekly (retrains), or post-slate (grading). Result: 60+ min per cron, most of it unnecessary.

Post Batch 1 optimizations (`2ba623cf` + friends), we're down to ~30 min per cron. This split takes it to **~20 min AM / ~5 min PM / <2 min intraday**.

## Proposed Split (7 workflows)

### `mlb_daily_am.yml` — 6am ET (single cron)
Everything needed to publish the morning card. **Est. runtime: 20 min.**

Steps in order:
1. `pitcher_stats.py` · `bullpen_stats.py` · `team_stats.py` (parallel-safe)
2. `savant_enrichment.py` · `savant_pitcher_arsenal.py`
3. `game_context.py`
4. `backfill_team_tendencies.py` · `enrich_team_form_universal.py --sport MLB` · `pull_teamrankings_trends.py --sport MLB` · `enrich_team_trends.py --sport MLB`
5. `mlb_advanced_metrics.py`
6. `verify_starters.py`
7. **`warmup_pitcher_buckets.py --workers 6`** (already shipped `bf830577`)
8. `_data_quality_audit.py`
9. `team_hr_threats.py`
10. `compute_pitcher_class_projections.py` · `patch_projected_ks.py`
11. `enrich_monte_carlo.py`
12. `recompute_primary_play.py`
13. `compute_l5_pitcher_actuals.py`
14. `build_hr_watch.py`
15. `generate_props.py`
16. `sweep_prop_coverage.py`
17. `apply_juice_trap_gate.py`
18. `compute_prop_bucket_roi.py` · `compute_game_bucket_roi.py`
19. `apply_prop_refit.py`
20. `backfill_prop_lookback.py --sport MLB`
21. `prop_ensemble_scorer.py --sport MLB --refresh-existing`
22. `generate_prop_jerry_synthesis.py --force --tier-gate PRIME,STRONG` (already shipped `2ba623cf`)
23. `collapse_prop_jerry_contradictions.py` · `collapse_pitcher_thesis_contradictions.py`
24. `apply_refit_verdict_override.py` · `apply_fade_type_discipline.py`
25. `apply_prop_signal_override.py` · `write_prop_reverse_signals.py`
26. `generate_daily_degen.py` · `generate_dawg_of_day.py` · `jerry_anchor_daily_degen.py`
27. `retry_missing_starters.py`
28. `generate_mlb_game_reads.py --force` (or consolidated Batch 3a script)
29. `generate_jerry_synthesis.py --force`
30. `collapse_sharp_fade_violations.py` · `reconcile_jerry_to_primary.py --sport MLB`
31. `conviction_calibration_pass.py --sport MLB`
32. `jerry_pre_publish_audit.py --sport MLB --repair`
33. `play_of_day.py` · `generate_potd_narrative.py --force`
34. `jerry_anchor_potd.py --threshold 70`
35. `generate_sweat_card.py`
36. `apply_prop_refit.py` (post-card hydration)
37. `snapshot_pick_lock.py --sport MLB --source card_lock`
38. Optional: `pull_alt_lines.py` (already gated to AM cron)

### `mlb_daily_pm.yml` — 2pm ET
Late-day refresh + tomorrow preview + content commit. **Est. runtime: 5 min.**

1. `pitcher_stats.py --imminent` (in case anything changed)
2. `verify_starters.py`
3. `warmup_pitcher_buckets.py` (late-confirm safety)
4. `retry_missing_starters.py`
5. `refresh_imminent_games.py`
6. `generate_prop_jerry_synthesis.py --force --tier-gate PRIME,STRONG`
7. `generate_sweat_card.py` (refresh)
8. `pull_externals_mlb.py --refresh`
9. Tomorrow preview (existing `Build tomorrow preview slate` block)
10. `generate_tonight_card.py`
11. `git commit content/*.md`

### `mlb_intraday.yml` — every 15 min game window
Merges current `mlb_line_poller.yml` + `mlb_imminent_refresh.yml` + `mlb_starter_retry.yml`. **Est. runtime: <2 min.**

1. `line_poller.py`
2. `refresh_imminent_games.py`
3. `classify_line_moves.py --sport ALL`
4. `apply_prop_refit.py`
5. `retry_missing_starters.py` (idempotent)

### `mlb_grading.yml` — midnight ET (post-slate)
Everything that grades yesterday. **Est. runtime: 3-5 min.**

1. `grade_props.py`
2. `grade_pick_snapshots.py --days 7`
3. `grade_prop_playbook.py --backfill 3`
4. `audit_prop_playbook_shadow.py --days 14`
5. `resolve_daily_degen.py`
6. `resolve_nrfi.py`
7. `resolve_game_results.py`
8. `grade_daily_card.py`
9. `compute_clv.py --sport ALL`
10. `grade_jerry_reads.py` · `grade_prop_jerry_reads.py`
11. `rescore_signal_registry.py`
12. `resolve_potd.py`
13. `resolve_game_results.py --card-only`
14. `resolve_externals.py --sport MLB --days 7`
15. `audit_external_source_calibration.py --sport MLB`
16. `audit_sharp_source_calibration.py --sport MLB`
17. `audit_consensus_bucket_calibration.py --sport MLB`
18. `grade_ledger_snapshots.py --days 7`
19. `aggregate_daily_records.py --backfill 1`
20. `snapshot_mlb_game_context.py`
21. `compute_model_track_records.py`
22. `check_pipeline_health.py`

### `mlb_weekly.yml` — Mon 8am ET
Model retrains + digests. Merges `mlb_refit_weekly.yml`. **Est. runtime: 15 min (retrain-heavy).**

1. `refit_train_v2.py`
2. `refit_signal_registry.py --days 90 --min-n 10`
3. `umpires_scrape.py`
4. `refresh_jerry_prompt_context.py --sport MLB`
5. `sharp_money_weekly_digest.py`
6. `clv_weekly_rollup.py --sport ALL`
7. Git commit updated model weights

### `cross_sport_shared.yml` — 4am ET (single cron)
Work that spans multiple sports, moved out of MLB cron. **Est. runtime: 5 min.**

1. `compute_scenario_audit.py --sport MLB --window lifetime`
2. `compute_scenario_audit.py --sport MLB --window 90d`
3. `compute_scenario_audit.py --sport MLB --window 30d`
4. `compute_scenario_audit.py --sport NCAAF --window lifetime`
5. `compute_scenario_audit.py --sport NHL --window lifetime`
6. `compute_scenario_audit.py --sport NCAAB --window lifetime`
7. `compute_sharp_scenario_matrix.py --sport MLB --window 90`
8. `backfill_signal_tiers.py --days 60 --sport ALL`
9. `audit_prop_signals.py --days 90 --sport ALL`
10. `backfill_prop_signal_tiers.py --days 60 --sport ALL`
11. `compute_hit_rate_dashboard.py --sport ALL`
12. `compute_rule_fire_stats.py --sport ALL`
13. `alert_dashboard_anomalies.py`
14. `check_data_quality_daily.py`
15. `dispatch_user_notes.py`
16. `backtest_rules.py`

### `mlb_line_ingestion.yml` — merged from `mlb_oddscrowd_refresh.yml`
Already runs 6× daily. No structural change — just move `archive_public_splits.py --sport ALL` and `write_line_snapshot.py --sport MLB` here.

### Retire / consolidate
- `mlb_watchdogs.yml` — keep as-is (2h cadence, standalone)
- `mlb_line_poller.yml` — MERGED into `mlb_intraday.yml`
- `mlb_imminent_refresh.yml` — MERGED into `mlb_intraday.yml`
- `mlb_starter_retry.yml` — MERGED into `mlb_intraday.yml`
- `mlb_close_line_capture.yml` — keep (specific 11am cadence)
- `mlb_prop_calibration.yml` — keep (specific 4:30am cadence)
- `mlb_refit_weekly.yml` — MERGED into `mlb_weekly.yml`

## Migration Steps (for the implementation session)

1. **Draft phase (safe):** create `.github/workflows/_drafts/*.yml` with the new structure. Test workflow_dispatch on staging branch.
2. **Parallel-run phase (1-2 days):** activate new workflows alongside old `mlb_pipeline.yml`. Compare outputs. `mlb_pipeline.yml` still authoritative.
3. **Cutover:** move new workflows out of `_drafts/`, disable old workflow, monitor for 3 days.
4. **Cleanup:** archive `mlb_pipeline.yml` to `_legacy/workflows/`, delete merged sub-workflows.

## Estimated cumulative pipeline impact after full split

| Cron | Before | After |
|---|---|---|
| MLB AM | ~30 min | ~20 min |
| MLB PM | ~30 min | ~5 min |
| MLB intraday | (3 crons × ~2 min) | 1 cron × <2 min |
| Grading | (mixed into AM/PM) | 3-5 min |
| Weekly | (mixed into AM Sundays) | 15 min (Mon only) |
| Cross-sport | (mixed into MLB) | 5 min (4am ET) |

**Net compute:** ~40% reduction in daily CI-minutes. Failures isolated by cadence — a grading bug can't tank the morning card build. Retries scoped to the workflow that failed.

## Non-goals (this proposal does NOT do)

- Consolidate `generate_mlb_game_reads.py` + `generate_jerry_synthesis.py` (Batch 3a — separate)
- Merge the 4 tier calibration layers (Batch 3b — separate)
- Rename or drop any tables

## Next step

Review this doc. When approved for implementation, we'll start with the Draft phase.
