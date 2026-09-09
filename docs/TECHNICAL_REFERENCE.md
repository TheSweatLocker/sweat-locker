# The Sweat Locker — Technical Reference Manual

Canonical technical reference for how the engine works. Written the way a manual for any powerful system would be written: enough detail to understand the mechanism, enough context to change it safely.

**Conventions**
- File paths are repo-relative
- Everything under `mlb_pipeline/` runs Python 3.11
- Everything under `.github/workflows/` runs on GitHub Actions
- Supabase is authoritative — every table, every read, every write
- Timestamps are UTC unless noted

**Structure** — one section per subsystem, each with: what it does, how it does it, where it lives, how to change it safely, current metrics.

---

## Table of contents

1. [Predictive Engine — Ensemble, Defensive Gates, LR Override](#1-predictive-engine)
2. [Logistic Regression System (Per Sport)](#2-logistic-regression-system-per-sport)
3. [NCAAF LR Deep Dive](#3-ncaaf-lr-deep-dive) ← 2026-09-08 build-out
4. [Historical Odds Backfill (CFBD)](#4-historical-odds-backfill-cfbd)
5. [Pipeline Map _(stub — pending buildout)_](#5-pipeline-map)
6. [Tier Engine _(stub)_](#6-tier-engine)
7. [Grading + Surface Records _(stub)_](#7-grading--surface-records)
8. [How to Add a New Sport _(stub)_](#8-how-to-add-a-new-sport)

---

## 1. Predictive Engine

**What it does.** Takes a game (with team ids, closing lines, injuries, tendencies, splits) and produces a `primary_play` — the single pick the app will surface for that game (side, market, tier, conviction, prose).

**The layers, in order:**

1. **Ensemble scorer v2** ([mlb_pipeline/ensemble_scorer.py](../mlb_pipeline/ensemble_scorer.py)) — weighted vote across ~20-40 signals per sport pulled from `signal_sources`. Produces per-market pick (ml, rl/spread, total) with tier + conviction.
2. **ML reroute** — if the picked market is ML and juice exceeds sport threshold (MLB -200, NCAAF/NFL -300, NBA/NCAAB -400), the engine swaps to spread or total instead.
3. **Defensive gates** ([mlb_pipeline/defensive_gates.py::apply_all_defensive_gates](../mlb_pipeline/defensive_gates.py)) — five sequential filters:
   - `oc_flip_gate` — reads Odds Chatter sharp-side signal; flips pick.side if OC contradicts strongly
   - `mc_dissent_gate` — demotes when Money Concentration disagrees with pick
   - `juice_trap_gate` — sport-specific juice cap → demote to LEAN
   - `ncaaf_large_spread_gate` — NCAAF-only, catches "Stanford +24.5"-class dogs
   - `apply_ml_lr_override` — sport-specific supervised model over-rides ensemble on ML
   - `apply_total_lr_override` — MLB/NCAAF/NFL sport-specific supervised model over-rides on Total
   - `publish_gate` — final tier ceiling based on signal count + refit backing
4. **LR shadow backfill** — even when LR doesn't override, its prediction is stamped on `primary_play._lr_ml_shadow` + `_lr_total_shadow` so post-hoc "would LR have picked this?" audits are possible for every game.

**Where the output lands.** `<sport>_game_context.primary_play` (jsonb). Downstream readers: `sync_jerry_reads_from_ctx.py` → `jerry_reads` → app surfaces (Sharp Card, Receipts, POTD, Game Detail).

**How to change it safely.**
- New signal → add row to `signal_sources` (sport, signal_key, weight, class, prose_template). No code change; ensemble picks it up on next run.
- New gate → append to `apply_all_defensive_gates` in canonical order. Wrap every gate in try/except that returns pp unchanged on failure — a bad gate must NEVER kill the pipeline.
- Sport-specific override → gate at line 1006-of-`defensive_gates.py` whitelist, matched by `sport` string.

---

## 2. Logistic Regression System (Per Sport)

**What LR is doing in laymans terms.** Eats numbers (closing market lines) and returns a probability ("home wins 62%"). Trained on thousands of past games. Overrides the ensemble on ML when it disagrees strongly + has enough historical rigor.

**Why market lines are the features.** Books bake insider info into the line — injuries, weather, sharp action, splits we don't have. The line is the smartest single-number summary of a game that exists. LR learns "when close_spread is -3.5 AND close_home_ml is -170 AND close_total is 47.5, home wins X% of the time historically."

**Model registry** — trained models live in [mlb_pipeline/models/](../mlb_pipeline/models/):

| File | Sport | Market | Status |
|------|-------|--------|--------|
| `mlb_ml_logreg.json` | MLB | Moneyline | ✅ trained + applied |
| `mlb_prop_logreg.json` | MLB | Props | ✅ trained + applied |
| `mlb_total_logreg.json` | MLB | Total | ✅ trained + applied |
| `nfl_ml_logreg.json` | NFL | Moneyline | ✅ trained + applied |
| `nfl_total_logreg.json` | NFL | Total | ✅ shadow-only |
| `ncaaf_ml_logreg.json` | NCAAF | Moneyline | ✅ trained + applied (retrained 2026-09-08) |
| `ncaaf_total_logreg.json` | NCAAF | Total | ✅ demote-only mode |
| `nba_ml_logreg.json` | NBA | Moneyline | ⛔ BLOCKED — 0/1324 historical close lines |
| `nhl_ml_logreg.json` | NHL | Moneyline | ⛔ BLOCKED — 0/1335 historical close lines |
| `ncaab_ml_logreg.json` | NCAAB | Moneyline | ⛔ BLOCKED — no historical corpus |

**Training pipeline** — one script per (sport, market):
- `<sport>_<market>_logreg_train.py` pulls resolved games from `<sport>_game_results`, drops rows missing any feature, fits sklearn `LogisticRegression` with `StandardScaler` + `SimpleImputer`, saves JSON of coefficients + intercept + feature order + scaler stats
- Refuses to train if all feature columns are 100% null (protects against silently-empty models)
- Weekly retrain cadence via [.github/workflows/mlb_refit_weekly.yml](../.github/workflows/mlb_refit_weekly.yml)

**Application pipeline** — inside `defensive_gates.apply_ml_lr_override`:
1. Load model JSON on module import (`_LR_MODEL_<SPORT>_ML` globals)
2. `_lr_predict_ml(ctx)` computes probability from ctx's close_home_ml / close_away_ml / close_spread / close_total
3. Decision table (simplified):
   - LR PRIME + agrees with ensemble → boost tier
   - LR PRIME + disagrees + juice below sport cap → OVERRIDE ensemble
   - LR PRIME + juice too heavy → shadow-only (don't override into juiced ML trap)
   - LR agrees but ensemble is LEAN → promote to STRONG
4. Always stamp `_lr_ml_shadow: {p_home_win, suggested_side, suggested_tier}` on pp so audits can reconstruct

**Where to interpret LR output** — every `primary_play` written after 2026-09-03 carries `_lr_ml_shadow` (dict, canonical) + `_lr_p_home_win` (float, legacy). Sharp Card composer, POTD gate, and audit scripts read `_lr_ml_shadow`.

---

## 3. NCAAF LR Deep Dive

Built out in one session 2026-09-08. Serves as the reference example for how LR ships end-to-end.

### 3.1 Training corpus (post-2026-09-08 backfill)

- **5,000 resolved NCAAF games** in training set (rows with all 8 features non-null: `open_home_ml, open_away_ml, open_spread, open_total, close_home_ml, close_away_ml, close_spread, close_total`)
- 6,122 games for the total model (looser feature requirements)
- Total resolved rows in `ncaaf_game_results`: 15,342 across 2022-2025 + 2026 in-season
- The gap (5k trained / 15k total): games with null closing lines, almost entirely FCS/D-II matchups that books never lined publicly

### 3.2 ML model metrics (retrained 2026-09-08 post-backfill)

```
Train  62.8%   Test  63.3%   Baseline  58.8%   Lift  +4.5pp
```

Per-bucket hit rate (test set):
| Bucket | Hits / N | % |
|--------|----------|---|
| PRIME_HOME | 231 / 257 | **89.9%** |
| STRONG_HOME | 562 / 1007 | 55.8% |
| STRONG_AWAY | 19 / 36 | 52.8% |
| PRIME_AWAY | 107 / 147 | 72.8% |
| COIN_NONE | 0 / 53 | (unpicked) |

**Feature coefficients** (after StandardScaler; sign indicates direction):
```
close_total    +1.781    largest positive weight — high totals correlate w/ home
open_total     -1.721    open-close movement matters
open_spread    -0.572
close_spread   -0.524    negative spread favors home (as expected)
close_home_ml  +0.031    small; ML redundant w/ spread
close_away_ml  -0.002    negligible
```

**Interpretation.** The model finds most of its signal in the *close_total* + *open_total* differential — how the market moved on the total tells LR who wins. Spread + ML are secondary. This matches the industry belief that sharp money flows into totals more than sides in college football.

### 3.3 Total model metrics

```
Train  51.9%   Test  51.8%   Baseline  50.3%   Lift  +1.5pp
```

Weak lift → runs in **demote-only** mode inside `apply_ncaaf_total_lr_override` (can't manufacture PRIME from weak signal, CAN kill obviously-wrong legacy totals). PRIME buckets in test have tiny samples (n=3-4) — need more data before promoting to full override.

### 3.4 Application in the live pipeline

Every ~40 min when `ncaaf_pipeline.yml` fires:
1. `ncaaf_game_context.py` builds today's games with closing lines
2. `_apply_ensemble` writes `primary_play` from ensemble_scorer.v2
3. `apply_all_defensive_gates(pp, ctx, sport='NCAAF')` runs
4. Inside gates: `apply_ml_lr_override(pp, ctx, sport='NCAAF')` fires
   - Reads `_LR_MODEL_NCAAF_ML` (loaded once at import)
   - Computes `p_home_win` from the 8 features
   - If disagrees + not too juiced + PRIME confidence → overrides ensemble
   - Always stamps `_lr_ml_shadow` for audit
5. `apply_ncaaf_total_lr_override(pp, ctx)` runs (demote-only)
6. Publish gate finalizes tier
7. `sync_jerry_reads_from_ctx.py --sport NCAAF` bridges to `jerry_reads` → app

**Verified 2026-09-08 on live slate (9/8-9/14):** 43/50 games have `_lr_ml_shadow`, 45/50 have `_lr_total_shadow`. Missing 5-7 are FCS games without lines (LR falls back gracefully when features null).

### 3.5 Weekly retrain loop

[.github/workflows/mlb_refit_weekly.yml](../.github/workflows/mlb_refit_weekly.yml) runs Sunday, in order:
1. `ncaaf_historical_odds_backfill.py --year 2026` — pull latest in-season fills from CFBD
2. `ncaaf_ml_logreg_train.py` — retrain on augmented corpus
3. `ncaaf_total_logreg_train.py`
4. Git-commit updated `models/*.json` if coefficients drifted

Training corpus grows every week as more games resolve + more lines land.

---

## 4. Historical Odds Backfill (CFBD)

**What it does.** Patches null `close_home_ml / close_away_ml / close_spread / close_total` on `ncaaf_game_results` using the CFBD `/lines` API endpoint. Idempotent — only writes into null fields, never overwrites existing live-captured lines.

**Where it lives.** [mlb_pipeline/ncaaf_historical_odds_backfill.py](../mlb_pipeline/ncaaf_historical_odds_backfill.py)

**Provider priority** (matches modern US closing market):
1. DraftKings
2. Bovada
3. ESPN Bet
4. any other provider present

Each field picked independently — if DK has spread but Bovada has ML, take spread from DK and ML from Bovada.

**Rate cost.** CFBD is billed per-request, free tier is 1000/mo. This backfill uses 2 requests per season (regular + postseason) × N seasons. Weekly in-season update = 2 requests. Negligible budget.

**Live run results 2026-09-08 (2022-2025):**
| Year | Patched | Fills (spread / total / home_ml / away_ml) |
|------|---------|--------------------------------------------|
| 2022 | 84 | 0 / 1 / 82 / 82 |
| 2023 | 181 | 3 / 39 / 138 / 145 |
| 2024 | 16 | 4 / 5 / 11 / 10 |
| 2025 | 60 | 1 / 2 / 59 / 58 |
| **Total** | **341** | 8 / 47 / 290 / 295 |

**Ceiling.** ~40% spread/total coverage, ~22% ML coverage is the practical max because FCS/D-II games (~60% of NCAAF rows) were never publicly lined. No source can fill them. The 40%/22% is complete over the universe of *publicly-lined games*.

**Cross-sport status:**
| Sport | Historical odds coverage | LR blocked? |
|-------|--------------------------|-------------|
| MLB | high (native pipeline capture) | No |
| NFL | 100% (nflverse CSV) | No |
| NCAAF | 20-41% (CFBD; capped by FCS/D-II gap) | No |
| NBA | 0% | **YES** |
| NHL | 0% | **YES** |
| NCAAB | 0% | **YES** |

NBA/NHL/NCAAB need a paid historical odds source (TheOddsAPI paid tier or SportsGameOdds) to unblock LR. Tracked in v1.0.1 priorities.

---

## 5. Pipeline Map

_Stub._ Every workflow, its schedule, its outputs, its heartbeat pings. Draft candidate list:
- `mlb_pipeline.yml` — 4 crons + push + manual; concurrency queue as of 2026-09-08 (0d798915)
- `mlb_grade_overnight.yml`
- `ncaaf_pipeline.yml`
- `nfl_pipeline.yml`
- `nba_pipeline.yml`
- `nhl_pipeline.yml`
- `ufc_pipeline.yml`
- `mlb_refit_weekly.yml` — Sunday, all-sport LR retrain
- `mlb_lr_retrain_weekly.yml` (verify — may be same file)

To be built out.

---

## 6. Tier Engine

_Stub._ The canonical tier assignment path, tier thresholds by sport, publish gate rules.

---

## 7. Grading + Surface Records

_Stub._ How yesterday's picks become today's headline. Covers:
- Per-sport resolver scripts (`<sport>_resolve_results.py`)
- `grade_jerry_reads.py` — writes result back to jerry_reads
- `compute_surface_records.py` — writes `surface_records` unified table
- `daily_surface_records` per-day rollups
- Which surfaces exist per sport (`mlb_sides`, `ncaaf_sides`, `sharp_card`, `prop`, `ledger`, `dawg`, `ladder`, `potd`, `ufc_sides` as of 38b61fd7)

---

## 8. How to Add a New Sport

_Stub._ Checklist for plugging a new sport in without breaking the 6 wired ones. Covers:
- Data pull scripts + `<sport>_game_context.py`
- `signal_sources` rows
- `ensemble_scorer` sport registration
- `defensive_gates` sport whitelist
- `<sport>_pipeline.yml` workflow
- `sync_jerry_reads_from_ctx.py` SPORT_CONFIG entry
- LR trainer scripts (train + predict)
- `compute_surface_records.py` picker function
- Grading + resolver

---

## Change log

| Date | Author | What |
|------|--------|------|
| 2026-09-08 | first draft | Initial structure + NCAAF LR deep dive (sections 1-4). Sections 5-8 stubbed. |
