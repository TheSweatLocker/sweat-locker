---
name: project-pipeline-overhaul-909
description: "🟢 P0 items resolved 9/15 audit. Heartbeat parity on all 8 pipelines, jerry_reads writer guards in place (sync bridge skips real synth, synth skips real synth). P1/P2 refactors still queued but no active data-loss race."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-15T17:13:18.117Z
---

## ✅ 2026-09-15 audit — P0 items verified resolved

Re-audit vs original 9/8 memo:

1. **Heartbeat parity** ✅ — `workflow_heartbeat`/healthcheck.io present on all 8 sport pipelines (nfl, ncaaf, ncaab, nba, nhl, ufc, mlb, mlb_grade_overnight). No silent-failure exposure on non-MLB sports.
2. **collapse_sharp_fade_violations cross-sport pollution** ✅ — mlb_pipeline.yml:1825 invokes with explicit `--sport MLB`.
3. **jerry_reads writer race** ✅ — traced MLB chain end-to-end:
   - Order: generate_prop_jerry_synthesis → apply_refit_verdict_override → generate_mlb_game_reads (writes jerry_cache) → generate_jerry_synthesis (writes jerry_reads w/ guard) → sync_jerry_reads_from_ctx (writes jerry_reads w/ guard)
   - `generate_jerry_synthesis` L847: skips games where `prompt_version = synthesis_v1` exists (unless --force)
   - `sync_jerry_reads_from_ctx` L120-132: skips games where prompt_version does NOT end in `_ctx_bridge_v1` (i.e. skips real synth rows)
   - `jerry_pick_scrub` only patches call_* fields, leaves prose untouched (per L30 comment)
   - `apply_refit_verdict_override` writes to prop_jerry_reads not jerry_reads (different table)
   - Same guard pattern on NFL/NCAAF (generate_nfl_game_reads runs BEFORE sync bridge in nfl_pipeline.yml, and sync bridge has skip guard)

## P1/P2 still queued (post-stabilization refactor)

- **Universal sport pipeline matrix** — 80% copy-paste across 5+ pipelines could collapse to one YAML. Cosmetic, not correctness.
- **Split 12:00 UTC pile-up** — 7+ workflows fire at exact same cron. GH Actions handles concurrency; latency is the only cost.
- **`_*.py` one-shot backfill cleanup** — these are gitignored per `feedback_underscore_scratch_convention` (visible as `??` in git status but not committed). No tree cruft.
- **Deduplicate WITHIN workflows** — `compute_external_source_records.py` still called 2× in mlb_pipeline.yml. Minor perf. Backlog.
- **Retire dead workflows** — `steam_room_fix.yml`, `mlb_watchdogs.yml`, `keep_alive.yml`. Verify each is truly dead before removal.

## Original 2026-09-08 audit below

**User surfaced 9/8 evening:** "The pipeline is in bad shape and probably has redundant workflows that have been here forever and other workflows overwriting stuff which has led to game read issues across all sports."

**Two parallel audit agents confirmed the diagnosis is correct.**

## The three real problems

### 1. NO ALERTING on 6 of 7 sport pipelines (SILENT FAILURE = STALE READS)
Only `mlb_pipeline.yml` + `mlb_grade_overnight.yml` have healthcheck.io + workflow_heartbeat DB inserts.

**Zero alerting on:** ncaaf, ncaab, nfl, nba, nhl, ufc, mlb_line_poller, mlb_imminent_refresh, mlb_oddscrowd_refresh, mlb_close_line_capture, mlb_starter_retry, mlb_prop_calibration, mlb_refit_weekly, mlb_watchdogs, steam_room_fix, sport_state_auto_flip, keep_alive.

Every step uses `continue-on-error: true` + `|| echo "…non-fatal"` — even hard failures inside runs don't fail the workflow. **This is exactly why user sees stale reads across sports without warning.**

### 2. THREE full-row upserters + 6 PATCH writers back-to-back on jerry_reads
MLB pipeline (mlb_pipeline.yml) chain writes to jerry_reads at line numbers:
1. `jerry_pick_scrub` @446 (call_* patch)
2. `generate_prop_jerry_synthesis` @810 → `apply_refit_verdict_override` @860
3. `generate_mlb_game_reads` @1607 (**full upsert**)
4. `generate_jerry_synthesis` @1668 (**full upsert** — LLM prose)
5. `sync_jerry_reads_from_ctx --sport MLB` @1674 (**full upsert** — has skip-guard for prompt_version≠bridge, but only ONE guard)
6. `collapse_sharp_fade_violations` @1692 (patch call_*, **defaults `--sport ALL`** — runs on every sport from MLB cron!)
7. `reconcile_jerry_to_primary` @1709 (patch call_side)
8. `jerry_pre_publish_audit --repair` @1754 (Layer D re-scrub — patches call_text/short_read/long_read/conviction)
9. Rescue re-runs `jerry_pick_scrub` + `apply_refit_verdict_override` @2066 + @2433

**Root pattern:** every writer uses same natural key (sport, game_id, game_date) but no writer checks "is this field already authoritative?" before overwriting. Last writer wins.

### 3. MAJOR pile-up at 12:00 UTC (race conditions)
7+ workflows fire at the exact same minute Sun-Mon-Sat depending on sport:
- nba_pipeline, nhl_pipeline, mlb_refit_weekly, nfl_pipeline, ncaaf_pipeline, mlb_oddscrowd_refresh, mlb_line_poller

Plus duplicate script invocations WITHIN and ACROSS workflows:
- `resolve_game_results.py` — mlb_pipeline AND mlb_grade_overnight (both write `mlb_game_results`)
- `game_context.py` — mlb_pipeline AND mlb_close_line_capture (both overwrite `primary_play`)
- `recompute_primary_play.py` — mlb_pipeline AND mlb_imminent_refresh (every 30m)
- `apply_prop_refit.py` — same pattern
- `compute_external_source_records.py` — TWICE inside mlb_pipeline.yml itself
- `refresh_prop_signal_calibration.py --sport NBA` — TWICE inside nba_pipeline.yml itself

## Dead / suspect workflows

- `steam_room_fix.yml` — dated one-off (`fix_steam_room_824.py`), manual-only
- `mlb_watchdogs.yml` — 6×/day, undocumented purpose
- `keep_alive.yml` — every 30m heartbeat, redundant with healthcheck.io + workflow_heartbeat DB
- `mlb_prop_calibration.yml` — parallel duty vs `mlb_refit_weekly` + inline calls in main pipeline

## 80% copy-paste across sport pipelines

Every sport workflow calls the same universal script set independently:
- enrich_team_form_universal.py, pull_teamrankings_trends.py, enrich_team_trends.py
- pull_scoresandodds.py, splits_v2_pipeline.py, refresh_prop_signal_calibration.py
- audit_external_source_calibration.py, audit_sharp_source_calibration.py
- compute_signal_patterns.py, backfill_prop_lookback.py, prop_ensemble_scorer.py
- backfill_<sport>_team_tendencies.py, grade_jerry_reads.py

**One universal `sport_pipeline.yml` with a matrix on sport would collapse ~5 files.**

## Sport-cross-contamination risks

- `collapse_sharp_fade_violations.py` defaults `--sport ALL` and is invoked without a sport flag in `mlb_pipeline.yml:1692` — iterates every registered sport during MLB cron
- `sync_jerry_reads_from_ctx --window 7/8` for NFL and NCAAF sweeps N days forward — every forward-dated row gets re-upserted each run
- NCAAF game_date drift (fixed 9/8 via `_ncaaf_jerry_date_backfill_2026_09_08.py`)
- 6+ one-shot hotfix `_*.py` scripts still in tree (should be deleted after backfill)

## Priority action list (post-launch)

### 🔴 P0 — Ship in v1.0.1 hotfix window
1. **Add healthcheck.io + workflow_heartbeat DB inserts to all 6 non-MLB pipelines** (silent failure = stale reads = user's complaint)
2. **Fix `collapse_sharp_fade_violations --sport MLB`** explicit flag in mlb_pipeline.yml (stop it iterating all sports)
3. **Delete `_*.py` one-shot backfill scripts** already applied (git rm)

### 🟡 P1 — Sprint after launch stabilizes
4. **Consolidate MLB write chain** — audit the 3 full-row upserters and add "skip-if-populated" guards uniformly (only 1 has it)
5. **Split the 12:00 UTC pile-up** — stagger sport crons by 5 min (nba 12:00, nhl 12:05, nfl 12:10, ncaaf 12:15)
6. **Retire `steam_room_fix`, `keep_alive`, `mlb_watchdogs`** — remove dead workflows
7. **Deduplicate calls WITHIN workflows** — `compute_external_source_records.py` called 2× inside mlb_pipeline.yml, same for nba

### 🟢 P2 — Post-stabilization refactor
8. **Universal sport pipeline** — matrix build one YAML replaces 5 sport-specific workflows
9. **Write-authority pattern** — add `authority_layer` enum column so no writer overwrites higher-priority prose (LLM > sync > scrub)
10. **Purge one-shot hotfix scripts** — nightly cron that git rms `_*.py` files older than 30d

## Related memory
- [[project_data_infrastructure_priorities_908]] — Priority 1/2/3 stack
- [[project_supabase_health_audit_909]] — Supabase advisor 65 issues (RLS fixed)
- [[feedback_migration_pgrst_reload]] — universal migration hygiene
- [[project_ui_toggle_infrastructure_908]] — parallel infra work (config_ui_sections)

## Key files
- All 19 workflows: `.github/workflows/*.yml`
- Full report: this memory
- MLB chain: `mlb_pipeline.yml:1607-1754, 2066, 2433`
- Writer scripts: `mlb_pipeline/{jerry_pick_scrub, generate_jerry_synthesis, sync_jerry_reads_from_ctx, collapse_sharp_fade_violations, reconcile_jerry_to_primary, jerry_pre_publish_audit}.py`
