# PIPELINE_MAP — Every cron step, dead code, shadow tables

**Purpose**: Full inventory of what runs, what it produces, and who consumes
it. Reference for the streamline pass.

**Written**: 2026-08-28.

**Bottom line**: 151 steps run in MLB cron 3×/day (~450 script invocations
per day). 97 truly-dead scripts sit in `mlb_pipeline/` with no cron and no
importer. ~15 tables are shadow-writes the app never reads. Executing the
top 10 kills strips 15-20 min/day from MLB cron, deletes 97 files, and
restores single-writer-of-truth for 3 subsystems.

---

## Workflow cadence (all sport pipelines)

| Workflow | Crons/day | Notes |
|---|---:|---|
| `mlb_pipeline.yml` | 3 | 10 UTC + 12:30 UTC backup + 18 UTC |
| `mlb_line_poller.yml` | ~76 | every 15 min (10-04 UTC) |
| `mlb_imminent_refresh.yml` | ~15 | every 30 min (16-02 UTC) |
| `mlb_oddscrowd_refresh.yml` | 6-12 | 6/day EDT + 6/day EST (double-fire) |
| `mlb_close_line_capture.yml` | 1 | 15 UTC |
| `mlb_prop_calibration.yml` | 1 | 04:30 UTC (Mon weekly extra) |
| `mlb_refit_weekly.yml` | 1/wk | Mon 12 UTC |
| `mlb_starter_retry.yml` | 2 | 21 + 00 UTC |
| `mlb_watchdogs.yml` | 6 | every 2 hrs 16-02 UTC |
| `nfl_pipeline.yml` | 1 avg | 7/wk Tue-Mon |
| `ncaaf_pipeline.yml` | 1 avg | 5/wk |
| `ncaab_pipeline.yml` | 4 in-season | 14/20/11 UTC + Mon 15 UTC KenPom |
| `ufc_pipeline.yml` | 4/wk | Wed/Fri/Sat/Sun |
| `nba_pipeline.yml` | 2 | year-round (mostly no-op off-season) |
| `nhl_pipeline.yml` | 2 | year-round (mostly no-op off-season) |
| `sport_state_auto_flip.yml` | 1 | 5 UTC |
| `steam_room_fix.yml` | manual | never scheduled — date-stamped one-off |

---

## Top 10 kill / consolidate candidates (ranked)

### 1. Delete UFC block from `mlb_pipeline.yml` (steps 61-63)

`ufc_card_scraper_v3.py`, `ufc_score_card.py`, `ufc_resolve_fights.py` run
day-gated inside MLB cron but ALSO run from `ufc_pipeline.yml`. Duplicate
work, dupe API calls to ESPN, source of divergence when logic drifts.

- **Savings**: ~3 min × 3 crons/day = 9 min/day
- **Risk**: low — UFC pipeline already has proper mode gating

### 2. Delete 2 no-op stub steps (MLB cron steps 28 & 55)

Both are `echo` statements. #28: "Retrain prop refit v2 (Sundays only)" —
real code is in `mlb_refit_weekly.yml`. #55: "KenPom Monday" — real code
is in `ncaab_pipeline.yml`.

- **Savings**: near-zero runtime, eliminates 2 misleading GHA log entries
- **Risk**: zero

### 3. Kill shadow inference (MLB cron steps 90 + 91)

`shadow_total_inference.py` + `shadow_ml_inference.py` write to
`jerry_cache` every cron. Their audit counterparts (`audit_v7_shadow.py`,
`audit_ml_v1_shadow.py`) are DEAD since 6/23 — cutover never taken. Also
delete `train_ml_v1_production.py`, `train_total_v7_production.py`, and
old model files.

- **Savings**: ~90 sec/day + ~500 KB `jerry_cache` growth/day + 7 files
- **Risk**: low — outputs feed nothing

### 4. Consolidate signal-registry writers

3 scripts all mutate `signal_registry`/`signal_sources` with different
semantics:
- `refit_signal_registry.py` (Sun, 90d, min-n=10)
- `rescore_signal_registry.py` (nightly, different weight formula)
- `backfill_signal_tiers.py` (nightly, 60d)

Semantics drift; a signal's tier bounces mid-week because the three
writers disagree.

- **Recommendation**: keep `rescore` (nightly), keep `refit` (weekly),
  delete `backfill_signal_tiers.py`.
- **Savings**: ~3-5 min/cron + one source of truth
- **Risk**: medium — need to verify no downstream reads `backfill`-only outputs

### 5. Delete 97 truly-dead scripts

Zero cron references + zero importers. Priority deletes (each has an
active universal replacement):
- `generate_nfl_props.py` → `nfl_generate_props.py`
- `generate_nfl_sweat_card.py` → `generate_sweat_card.py`
- `sweep_nfl_prop_coverage.py` → `sweep_prop_coverage.py`
- `pull_ncaab_teamrankings_trends.py` → `pull_teamrankings_trends.py --sport NCAAB`
- `enrich_ncaab_team_trends.py` → `enrich_team_trends.py --sport NCAAB`
- `refit_train_v3.py` (dead alongside cron-wired `refit_train_v2.py`)
- `park_factors.py` (dead)
- 6× `backtest_model_attribution_v{1..6}.py` (6 iterative dupes)
- 4× `backtest_ensemble*.py` scratch versions
- 10× `seed_*_signal_sources.py` / `seed_*_prompt.py` one-shots

**Savings**: repo clarity, faster grep, ~2 MB codebase, one less
"which file is authoritative" question per week

### 6. Kill legacy `nfl_props` write path

`nfl_generate_props.py` dual-writes to `nfl_props` (legacy) AND
`nfl_pipeline_props` (canonical). Downstream reads only the canonical
table. Retarget `resolve_nfl_props.py` + `resolve_nfl_results.py` to
`nfl_pipeline_props` and drop the legacy schema.

- **Savings**: 1 shadow table + 1 dual-write per cron
- **Risk**: medium — need to update 2 resolvers

### 7. Consolidate 3 splits scrapers into 1 poller workflow

`fadereport_scraper.py`, `cleatz_scraper.py`, `pull_scoresandodds.py`
each run inside MLB cron + per-sport crons (8+ invocations/day). Move
to a single `splits_poller.yml` every 30-60 min feeding
`public_splits_v2`.

- **Savings**: ~5-10 min/day compute, cleaner per-sport crons
- **Risk**: medium — requires new workflow + rewire consumers

### 8. Gate `compute_scenario_audit.py` NHL/NCAAB calls (MLB cron step 33)

Both currently run every MLB cron 3×/day as silent no-ops ("results
tables don't exist yet"). Gate on `sport_registry.state == 'active'`.

- **Savings**: ~30 sec/day + honest GHA logs
- **Risk**: zero

### 9. Slow NBA + NHL cron to 1×/day pre-season

Both fire 2×/day year-round; all steps no-op until Oct 7/Oct 22
kickoff. Re-enable to 2×/day when preseason starts.

- **Savings**: 14 unnecessary runs/wk × ~5 min = 70 min/wk runner time
- **Risk**: zero (still get 1 daily run for state tracking)

### 10. Delete `mlb_prop_calibration.yml` if unused

Writes `prop_edge_calibration` + `prop_edge_backtest_history`. No
confirmed reader in app or scorer path. If confirmed unused, delete
workflow + 2 tables + 2 scripts.

- **Savings**: 1 daily + 1 weekly workflow + 2 shadow tables + ~10 min/wk
- **Risk**: medium — need to confirm no reader before dropping

---

## Bonus notes

- **`mlb_imminent_refresh.yml`** fires every 30 min for 15 hrs = 30 runs/day.
  Steps 4-5 (`classify_line_moves.py --sport ALL` + `apply_prop_refit.py`)
  already run inside main cron + oddscrowd_refresh. Consider whether the
  imminent-game refresh needs those two invocations at 30-min cadence.

- **`_data_quality_audit.py`** (underscored) is the ONLY underscored
  script invoked by cron (step 15). Rename or merge into
  `check_data_quality_daily.py` (step 144).

- **`mlb_starter_retry.yml`** + MLB main cron both call
  `retry_missing_starters.py`. Cheap idempotent, but move main-cron
  copy to fire only post-2pm.

- **`steam_room_fix.yml`** is manual-only and tied to date-stamped
  `fix_steam_room_824.py`. Delete workflow once the fix is retired.

---

## Full inventory

Full 151-step MLB cron table, per-sport pipeline breakdown, 97 dead
files with last-modified dates, 15+ shadow tables — see task audit
output at `.claude/tasks/a2dfb967712a1498b.output`.

## Change log

| Date | Change |
|---|---|
| 2026-08-28 | Doc created + 10 kill candidates ranked |
