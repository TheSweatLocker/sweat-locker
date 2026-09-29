---
name: ncaaf-phase1-complete-723
description: "NCAAF Phase 1 core loop shipped 7/23 — 5 items + workflow. Ready for Aug 22 kickoff. 15k games backfilled, cohorts baselined, SP+/EPA firing."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-24T01:26:19.420Z
---

**Set 2026-07-23 evening after full NCAAF Phase 1 completion.**

## Shipped (10 files + 1 migration + 1 workflow)

**Foundation** (SHA 99ae0df):
- `20260723c_ncaaf_foundation.sql` — 4 tables + RLS
- `ncaaf_seed_aliases.py` — 134 FBS teams (11 conferences)
- `ncaaf_odds_pull.py` — Odds API americanfootball_ncaaf
- `ncaaf_backfill_results.py` — CFBD /games + /lines backfill
- `ncaaf_game_context.py` — SP+/EPA projections + confluence + primary_play

**Fixes** (SHA 1e8212a):
- PGRST102 batch key normalization on backfill + odds pull
- 3 FBS team aliases patched (FIU, Sam Houston, UL Monroe)
- `ncaaf_stats_pull.py` — CFBD /stats/season/advanced + /ratings/sp

**Analytics** (SHA b5a7dd5):
- `ncaaf_cohort_backfill.py` — 8 cohorts on 15k games (canonical schema)
- `resolve_ncaaf_results.py` — post-game refresh + kicks resolve_externals

**Jerry + Externals** (SHA 5c28a5c):
- `generate_ncaaf_game_reads.py` — server-side Jerry read (mirrors NFL)
- `pull_externals_ncaaf.py` — CFB sport-slug external picks

**Workflow** — 5-cron NCAAF pipeline (Tue/Wed/Fri/Sat/Sun) with mode
detection, includes Jerry reads + externals + consensus fade detector
+ resolver + cohort refresh all wired sport-agnostic where possible.

## Live DB state

| Table | Rows |
|---|---|
| ncaaf_team_aliases | 134 |
| ncaaf_team_stats | 538 (4 seasons × ~135 teams) |
| ncaaf_game_results | 15,071 (6,018 w/ lines, 15,063 graded) |
| ncaaf_game_context | 0 (season starts Aug 22) |

## Cohort baseline (n=6,018 games w/ lines)

All cohorts hover 48-54% — no strong signals. Best: heavy_home_dog_7_13
53.9% n=658 (barely +1.5pt vs break-even). Real edge will live in
narrower cohorts (SP+ agreement with line, week 1-3 vs 4+). Baseline
established.

## Known limitations / follow-ups

1. **2026 preseason SP+ not yet published** by CFBD (drops mid-Aug).
   Model uses 2025 fallback until then — noisy early season expected.
2. **No weather data** from CFBD /games (temp/wind = null on all rows).
   Skipped outdoor cold/wind cohorts.
3. **NCAAF prompt_templates row missing** — Jerry falls back to NFL
   rules template. Add NCAAF-specific row during cross-sport Jerry
   read audit.
4. **Recent-mastery equivalent** — no per-team H2H recency in CFBD
   /games; would need separate endpoint. Skipped for v1.
5. **FCS opponents** intentionally skipped (v1.0 = FBS only).
6. **`nfl_cohort_backfill.py` uses wrong field names** — latent bug
   surfaced during NCAAF work. Rows likely didn't land in
   mlb_tier_calibration. Queue: fix + backfill.

## Model calibration targets

Current constants informed guesses — recalibrate after Week 4:
- K_PTS_SP = 0.85 (SP+ diff → spread pts)
- HOME_FIELD_PTS = 2.8
- BASE_TOTAL = 52.0

## Related

- [[project_ncaaf_scope]] — v1.0 spread/ML/total-only scope
- [[project_nfl_phase1_ready_721]] — NFL Phase 1 (mirror pattern)
- [[project_30d_model_audit_723]] — MLB lens performance baselines
- [[feedback_postgrest_batch_normalize_keys]] — batch upsert pattern
