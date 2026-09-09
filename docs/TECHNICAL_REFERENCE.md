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

### 3.5 NFL Prop Confluence Gate (2026-09-08)

**Purpose.** Assign tier + conviction to every NFL player prop using recency-weighted multi-signal confluence. Replaces the flat LEAN cap that was in place at end-of-day 2026-09-08.

**Where it lives.** [mlb_pipeline/nfl_prop_signal_discipline.py](../mlb_pipeline/nfl_prop_signal_discipline.py) — runs post-`nfl_generate_props.py` in `nfl_pipeline.yml` every workflow trigger.

**Rolling by design.** Every signal draws from windows that shift as new games play. `_stat_last10` array rolls each week. L4/L5/L10 averages recompute against fresh game logs. So tier assignments always reflect the freshest data — no periodic manual retrain.

**Signals + weights (recency-heavy):**
| Signal | Weight | What it checks |
|--------|--------|----------------|
| L4 avg agrees | 1.5 | Player's last-4 avg on pick's side of line |
| L5 avg agrees | 1.25 | Last-5 avg on same side |
| L10 avg agrees | 1.0 | Last-10 avg on same side (medium-term stability) |
| Season avg agrees | 0.75 | Full season avg on same side (dampened — old data) |
| L10 hit rate ≥ 60% | 1.25 | Direct pick-side hit rate from 10-game log |

Total possible: **5.75 pts**

**Tier ladder:**
| Tier | Edge floor | Score floor | Extra conditions |
|------|-----------|-------------|------------------|
| PRIME | ≥18% | ≥4.5 | Season avg MUST agree, L10 hit ≥70%, hit_n ≥7 |
| STRONG | ≥12% | ≥3.25 | — |
| LEAN | ≥8% | ≥2.25 | — |
| LIGHT | ≥5% | — | Bare edge, no confluence needed |
| SKIP | — | — | Below LIGHT OR insufficient valid signals (<2) |

**Two hard guards (added 2026-09-08 to prevent data-noise PRIMEs):**
1. **Extreme edge cap:** `edge_pct > 40` → force LEAN. Small-sample players + role misclassification + tiny lines produce implausible 300%+ edges. Real PRIMEs live in the 18-40% edge band.
2. **Null player_team guard:** any prop with `player_team IS NULL` capped at LEAN. Can't trust the pick belongs to this game.

**Cross-team leak guard.** [nfl_generate_props.py::build_prop_row](../mlb_pipeline/nfl_generate_props.py) rejects rows where `player_team not in (home_canon, away_canon)`. Prior state (pre-guard): A.J. Brown (PHI) props leaking into NE @ SEA game because Odds API matched a name and `player_id_lookup` returned his roster row without validation.

**Alt-line dedupe.** Same player + prop family (e.g., rush_yds) with multiple book lines dedupes to ONE winner per (game, player, family). Winner = highest conviction. Losers → SKIP. Prevents "same player STRONG OVER 44.5 AND STRONG UNDER 58.5" contradictions.

**Live distribution (2026-09-08 Week 1 slate, 426 props post-cleanup):**
- 39 PRIME · 89 STRONG · 149 LEAN · 145 LIGHT · 4 SKIP
- ~2-3 PRIMEs per game (16 games)

**Deferred to post-Week-1:** real `signal_sources` sport=NFL class=prop rows with actual hit_rate/n from graded prop outcomes. Would replace the hand-tuned edge/score thresholds with data-driven calibration. Tracked in [[project_nfl_prop_signal_gap_908]].

---

### 3.6 GOAT Model (NFL shadow, 2026-09-08)

**Purpose.** Fused-signal composite that runs in parallel to the NFL ensemble. Not overriding anything at launch — data collection so a real LR fit becomes viable by Week 4.

**Composite formula (v0 hand-weighted):**
```
score = 0.30 × ensemble_signal    (current NFL primary_play, tier→prob)
      + 0.25 × lr_shadow          (nfl_ml_logreg output)
      + 0.15 × talent             (Madden team + Madden QB + Top100 mix)
      + 0.10 × panel_proj         (Sleeper-derived panel_pred point diff)
      + 0.10 × injury_delta       (position-weighted status count, home vs away)
      + 0.05 × rest_advantage     (rest days delta, capped ±3)
      + 0.05 × weather            (wind >15 mph total penalty)
```

**Tier mapping:** `|score|>0.35 = PRIME`, `>0.15 = STRONG`, `>0.05 = LEAN`, else `COVERAGE`.

**Where it lives.** [mlb_pipeline/nfl_goat_composite.py](../mlb_pipeline/nfl_goat_composite.py). Runs post-context in `nfl_pipeline.yml`, before `compute_align_status_nfl.py`.

**Writes:**
- `primary_play._goat_shadow` — full breakdown `{score, tier, side, p_home, edge_home, contributions, weights, inputs, total, chip, model_version, computed_at}`
- The `chip` sub-object is picked up by [align_status_common.build_alignment](../mlb_pipeline/align_status_common.py) and appended to `align_status.chips_extra`, which the client renders in the alignment strip via `<InfoChip>`.

**Chip payload (client-facing):**
```json
{
  "key": "goat",
  "label": "GOAT",
  "value": "DAL · STRONG",
  "tooltip": "Proprietary predictive model...",
  "kind": "ok",         // ok = agrees w/ pick, warn = dissents, neutral = both PASS/no pick
  "priority": 25
}
```
Tooltip copy is TOS-scrubbed — no source name (Madden / Sleeper / LR) appears in user-facing text.

**Cadence.** Every NFL context run (Tue/Wed/Thu/Sat/Sun) + `--dry-run` supported for testing.

**Promotion path.** After Weeks 1-3 (~48 games w/ shadow + actual outcome), LR-fit on the fused feature vector. If lift ≥ +3pp vs current ensemble baseline, promote from shadow to weighted-vote in the ensemble. Same discipline as NCAAF LR closeout (2026-09-08).

**Backend-driven chip infrastructure.** `align.chips_extra` array is generic. Future models (NBA GOAT, prop GOAT, whatever's next) drop into the array server-side and surface in the app without a rebuild — no chip is hardcoded in TSX.

---

## 5. Pipeline Map

**Every workflow, its schedule, what it produces, and how to check it when it breaks.** 19 workflow files live in `.github/workflows/`. They fall into three tiers.

### 5.1 Sport pipelines (7 — one per active sport)

The main daily/weekly workhorse per sport. Each ends with heartbeat pings to `workflow_heartbeat` (start + end) so cross-sport dashboards can spot silent skips.

| Workflow | File | Schedule (UTC) | Concurrency | Heartbeat |
|----------|------|----------------|-------------|-----------|
| **MLB Pipeline** | `mlb_pipeline.yml` | 10:00, 11:15, 12:30, 18:00 daily + push on `mlb_pipeline/**` + manual | **queue** (`cancel-in-progress: false`) since 0d798915 | ✅ 4 pings |
| **NCAAF Pipeline** | `ncaaf_pipeline.yml` | Tue 15:00, Wed 22:00, Fri 18:00, Sat 12:15, Sun 13:00 | none | ✅ 4 pings |
| **NFL Pipeline** | `nfl_pipeline.yml` | Tue 15:00, Wed 12:10+22:00, Thu 12:10+18:00, Sat 14:00, Sun 12:10, Mon 14:00 + `*/6h` Thu-Sun | none | ✅ 2 pings |
| **NHL Pipeline** | `nhl_pipeline.yml` | 12:05 daily (staggered off 12:00 pile-up) | none | ✅ 2 pings |
| **NBA Pipeline** | `nba_pipeline.yml` | 12:00 daily (preseason placeholder) | none | ✅ 2 pings |
| **NCAAB Pipeline** | `ncaab_pipeline.yml` | 11:00 (resolver), 14:00 (morning), 20:00 (pre-tip) daily + Mon 15:00 (KenPom) | none | ✅ 2 pings |
| **UFC Pipeline** | `ufc_pipeline.yml` | Wed 22:00 (pull), Fri 14:00 (line moves), Sat 14:00 (early grader), Sun 14:00 (resolver) | none | ✅ 2 pings |

**Shape of every sport pipeline** (common step order — deviations per sport):
1. Heartbeat start
2. External monitor ping start (healthchecks.io if `HC_*_URL` secret set)
3. Resolve yesterday's results (`<sport>_resolve_results.py`)
4. Grade jerry_reads from yesterday's outcomes (`grade_jerry_reads.py --sport X`)
5. Build today's game_context (schedule + odds + rest + primary_play write via ensemble+gates)
6. Enrich context (team form, tendencies, injuries, splits, rankings)
7. Prop generation (if sport supports; NCAAF/NCAAB skip — see [[feedback_college_sports_no_props]])
8. Signal calibration refresh (per-sport hit-rate rollups)
9. Sync jerry_reads from primary_play (`sync_jerry_reads_from_ctx.py --sport X`)
10. Matview refreshes (team_recent_games, team_situational_records, team_stats_rolling)
11. Heartbeat end (fires with `if: always()` — never missing means workflow completed)

### 5.2 MLB support workflows (7 — MLB is the heaviest sport)

MLB has the most surface area (year-round + daily props + POTD lock + intraday refreshes), so it has 7 supporting workflows beyond the main pipeline.

| Workflow | Schedule (UTC) | Purpose |
|----------|----------------|---------|
| `mlb_grade_overnight.yml` | 06:30, 08:30, 09:30 daily | Grade last night's picks BEFORE morning MLB pipeline runs; writes `daily_best_bet_history` |
| `mlb_close_line_capture.yml` | 15:00 daily (11am ET) | Capture true closing lines to `mlb_game_results.close_*` for backtest / LR training |
| `mlb_line_poller.yml` | `*/15` during 10-23 UTC + 0-3 UTC | Snapshot line movement into `line_history` every 15 min during active hours |
| `mlb_imminent_refresh.yml` | `*/30` during 16-23 UTC + 0-2 UTC | Re-score games within 90 min of first pitch (last-mile lineup + injury updates) |
| `mlb_oddscrowd_refresh.yml` | 12/15/16/19/22 UTC + 1 + 13/16/17/20/23 UTC + 2 | Poll OddsCrowd sharp-side data (6-shot cadence to match their update pattern) |
| `mlb_starter_retry.yml` | 21:00, 00:00 UTC | Catch late-announced pitchers (5pm ET + 8pm ET retry windows) |
| `mlb_watchdogs.yml` | 16, 18, 20, 22 UTC + 0, 2 UTC | Run consistency_watchdog + smoke tests every 2 hrs during active hours |

### 5.3 Cross-cutting workflows (5)

| Workflow | Schedule (UTC) | Purpose |
|----------|----------------|---------|
| `mlb_refit_weekly.yml` | **Mon 12:00** | Retrain refit weights + ALL sport LR models (MLB/NFL/NCAAF applied; NBA/NHL/NCAAB exit gracefully w/o corpus). Runs NCAAF odds backfill first (2026-09-08+). Commits fresh JSON weights if drift. |
| `mlb_prop_calibration.yml` | 04:30 daily | Recompute `prop_signal_calibration` rolling hit rates |
| `sport_state_auto_flip.yml` | 05:00 daily | Flip `sport_states` (in-season / preseason / offseason) based on calendar |
| `keep_alive.yml` | Every hour | Ping to prevent GH from marking the repo inactive + skipping scheduled workflows |
| `steam_room_fix.yml` | Manual only | One-shot recovery for Sharp Card / Steam Room composition bugs |

### 5.4 Trigger chain (who fires what)

```
Time-based crons ──┐
                   ├─→ Sport pipelines ──→ jerry_reads / prop_jerry_reads / jerry_cache
Push on mlb_pipeline/** ──┘                    ↓
                                          App reads via Supabase
                                                ↓
Manual dispatches ──→ specific workflows       ↓
                                    (Yesterday's picks resolve →
                                     grade → surface_records rollup)
                                                ↓
mlb_grade_overnight ──→ daily_best_bet_history + POTD anchor lock

Weekly Mon 12:00 ──→ mlb_refit_weekly ──→ ncaaf_odds backfill
                                     ──→ retrain ALL LR models
                                     ──→ git commit weights (auto-push)
```

**Key handoffs**
- Ensemble → jerry_reads: via `sync_jerry_reads_from_ctx.py` (in each sport pipeline post-context step)
- Primary_play → daily_best_bet_history: via `reconcile_potd_surfaces.py` after `jerry_anchor_potd` (mlb_pipeline)
- Graded outcomes → surface_records: via `compute_surface_records.py` post-resolve
- Fresh LR weights → live picks: automatic on next context run (loaded on import in `defensive_gates.py`)

### 5.5 Concurrency + reliability

**Concurrency groups** — currently only mlb_pipeline has one (`group: mlb-pipeline, cancel-in-progress: false`, added 0d798915). Prior state: 23 concurrent MLB runs in a 4-hr window raced on Supabase upserts and tripped `resolve_potd` with transient collisions. Queue mode means late-arriving triggers wait; never kills an in-flight run at its final commit step. Other sport pipelines don't have this yet — MLB was the only one with the trigger volume to need it. Add if we see the same race pattern elsewhere.

**Heartbeat pattern** — every reliable workflow writes to `workflow_heartbeat` twice per run:
- `event=start` (first step)
- `event=end` (last step, `if: always()` — fires whether prior steps succeeded or failed)

Missing `end` for a `start` = job was killed externally (cancellation, hard timeout, or runner death). Query pattern to find zombies:
```sql
SELECT start.run_id, start.fired_at
FROM workflow_heartbeat start
LEFT JOIN workflow_heartbeat e ON e.run_id = start.run_id AND e.event = 'end'
WHERE start.event = 'start' AND e.run_id IS NULL
  AND start.fired_at > now() - interval '4 hours';
```

**External monitors** — most workflows also ping `HC_<SPORT>_URL` secret (healthchecks.io). Two-layer defense: heartbeat catches script-level failure, HC catches GH-Actions-level silent-skip failure (GH cron silently drops scheduled runs when under load — happened for a week in late Aug 2026).

**Belt-and-suspenders redundant crons** — mlb_pipeline has 3 morning triggers (10:00, 11:15, 12:30 UTC) so even if 2/3 drop, the third fires. Later runs no-op via idempotent writes.

### 5.6 Where to look when something breaks

| Symptom | First place to check |
|---------|----------------------|
| Alert email says pipeline failed but GH shows success | Concurrency race — query `workflow_heartbeat` for starts w/o ends around the alert time |
| App shows stale data | `jerry_reads.updated_at` for that (sport, date); if stale → check sync_jerry_reads step |
| Sharp Card missing picks for a sport | `jerry_cache.cache_key='sharp_card_YYYY-MM-DD'` `.fetched_at`; if fresh but empty → composer bug |
| POTD not showing | `jerry_cache.cache_key='best_bet_YYYY-MM-DD'` + `daily_best_bet_history` for that date |
| LR shadow missing on games | `primary_play._lr_ml_shadow` — null usually means close lines weren't populated in ctx at write time |
| Grading behind (records stale) | `surface_records` `updated_at` per sport; if stale → check `compute_surface_records` in overnight/pipeline logs |
| Cron silently skipped | GH Actions runs history; if no run appears at expected time → HC alert triggers (or should) |
| Heartbeat table shows no entries | `workflow_heartbeat` writes need `SUPABASE_SERVICE_ROLE_KEY` secret; check workflow env vars |

**Morning audit** — running [`mlb_pipeline/morning_audit.py`](../mlb_pipeline/morning_audit.py) prints a green/warn/red board of everything above in ~30 sec. Should be the daily first-check before opening the app.

---

## 6. Tier Engine

_Stub._ The canonical tier assignment path, tier thresholds by sport, publish gate rules.

---

## 7. Grading + Surface Records

**How yesterday's picks become today's headline.** Every pick the engine publishes goes through four stages: **resolve** the game outcome → **grade** each open pick against it → **compose** per-surface pick lists → **aggregate** into windowed records the app reads.

### 7.1 Stage 1 — Resolve game outcomes

Per-sport script pulls final scores + market outcomes into `<sport>_game_results`. Runs in the sport pipeline post-daily-cron (see §5).

| Sport | Resolver script | Result columns |
|-------|-----------------|----------------|
| MLB | `resolve_mlb_results.py` | `home_score, away_score, home_win, run_line_result, total_result` |
| NFL | `resolve_nfl_results.py` (nflverse) | `home_score, away_score, home_win, spread_result, total_result` |
| NCAAF | `resolve_ncaaf_results.py` (CFBD) | same as NFL |
| NBA | `nba_resolve_results.py` | same as NFL |
| NHL | `nhl_resolve_results.py` | + `went_to_ot, went_to_so, close_puckline` |
| NCAAB | `resolve_ncaab_results.py` | same as NFL |
| UFC | inline in `ufc_picks` (no separate results table) | `winner_actual, method_actual, rounds_actual, distance_actual` |

**Failure mode: 400-error on grader.** MLB uses `run_line_result` column (±1.5 concept), others use `spread_result` (point spread). Prior versions of the grader hardcoded `run_line_result` in the SELECT → 400 on non-MLB sports → silent no-op. Fixed 2026-09-07 with per-sport column dispatch.

### 7.2 Stage 2 — Grade jerry_reads

[`mlb_pipeline/grade_jerry_reads.py`](../mlb_pipeline/grade_jerry_reads.py) is the sport-universal grader.

**Registry** (line 29):
```python
RESULTS_TABLE = {
    'MLB': 'mlb_game_results', 'NBA': 'nba_game_results',
    'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results', 'NHL': 'nhl_game_results',
}
```
UFC intentionally excluded — different pick model (fighter A vs B, not team side vs total).

**Flow per row:**
1. Pull ungraded `jerry_reads` rows (result IS NULL) for the sport
2. Join to `<sport>_game_results` on game_id
3. Interpret the pick against outcome:
   - `type=ml`, `side=HOME`, `home_win=true` → `result=win`
   - `type=rl|spread`, `side=HOME`, `spread_result=home_covered` → `win`
   - `type=total`, `side=OVER`, `total_result=over` → `win`
   - Push conditions (spread_result='push') → `result=push`
4. Compute `units_net` from odds if present, else flat -110 (`payout: 0.909`)
5. UPSERT `jerry_reads` with `result, units_net, graded_at`

**Called from:** each sport pipeline's "Grade jerry_reads" step, immediately after resolve. Backfills any ungraded historical rows on each run (idempotent).

### 7.3 Stage 3 — Compose per-surface pick lists

[`mlb_pipeline/compute_surface_records.py`](../mlb_pipeline/compute_surface_records.py) is the aggregator. It has **one picker function per surface**, each returning a flat list of graded picks `[{sport, date, result, stake, payout}, ...]` that `_aggregate` rolls up.

**Current surface registry** (19 surfaces, line 710):

| Surface | Sport scope | Source | What it counts |
|---------|-------------|--------|----------------|
| `sharp` | MLB | mlb_game_context.primary_play (PRIME/STRONG) | Legacy MLB sides |
| `prop` | MLB | mlb_pipeline_props (PRIME+STRONG bundled) | Legacy prop record |
| `sharp_card` | ALL | jerry_cache.sharp_card_YYYY-MM-DD | 🎯 **Authoritative combined sides+props** (2026-09-05) |
| `ladder` | MLB | jerry_cache.ladder | Steam Room Ladder (1/day roll-winnings) |
| `ledger` | MLB | jerry_cache.ledger | Ledger parlays + teasers |
| `potd` | ALL | daily_best_bet_history | Play of the Day |
| `dawg` | MLB | daily_dawg | Dawg of the Day |
| `prop_prime` | MLB | mlb_pipeline_props (PRIME only) | 🎯 tier-split (2026-09-09) |
| `prop_strong` | MLB | mlb_pipeline_props (STRONG only) | tier-split |
| `prop_lean` | MLB | mlb_pipeline_props (LEAN only) | tier-split |
| `prop_coverage` | MLB | mlb_pipeline_props (COVERAGE only) | tier-split |
| `mlb_sides` | MLB | primary_play (PRIME/STRONG/LEAN) | 🎯 UNIFORM sides (2026-09-09) |
| `nfl_sides` | NFL | primary_play (PRIME/STRONG/LEAN) | UNIFORM sides |
| `ncaaf_sides` | NCAAF | primary_play (PRIME/STRONG/LEAN) | UNIFORM sides |
| `nba_sides` | NBA | primary_play (PRIME/STRONG/LEAN) | UNIFORM sides |
| `nhl_sides` | NHL | primary_play (PRIME/STRONG/LEAN) | UNIFORM sides |
| `ncaab_sides` | NCAAB | primary_play (PRIME/STRONG/LEAN) | UNIFORM sides |
| `ufc_sides` | UFC | ufc_picks (winner pick PRIME/STRONG/LEAN) | UFC sides (2026-09-08, 38b61fd7) |

**Design principle: uniformity.** Every sport gets a `<sport>_sides` surface with identical shape so Receipts renders the same way for every sport. Pre-2026-09-09, MLB had no `mlb_sides` and non-MLB had nothing — Receipts was inconsistent. `_pick_generic_sides()` helper (line 453) handles the common case; UFC is bespoke because its result model differs.

### 7.4 Stage 4 — Aggregate into windows

**Four windows** (line 46): `['mtd', 'd7', 'd30', 'lifetime']`

**Sports rolled up:** `['ALL', 'MLB', 'NFL', 'NCAAF', 'NBA', 'NHL', 'NCAAB', 'UFC']` (ALL = grand total across sports).

For each `(surface, sport, window)` combination, `_aggregate` computes:
- `wins, losses, pushes` counts
- `units_net` (Σ stake × payout for wins, minus stake for losses)
- `last_date_graded` (most recent resolution)
- `epoch_start` (earliest date graded within window)

**Output table.** `surface_records` (unique constraint on `sport, surface, window_key`). Upserted via PostgREST `on_conflict` merge-duplicates.

**Per-day rollups.** Separate table `daily_surface_records` (written by [`aggregate_daily_records.py`](../mlb_pipeline/aggregate_daily_records.py)) stores wins/losses per (sport, surface, date). Enables split-per-day drilldowns in the app (e.g., "yesterday MLB sharp_card went 6-2").

### 7.5 Client read pattern

**One fetch, cached:** [app/index.tsx:5518](../app/index.tsx#L5518)
```js
const {data} = await supabase.from('surface_records').select('*');
const map = {};
data.forEach(r => { map[`${r.sport}|${r.surface}|${r.window_key}`] = r; });
```

Every UI element that shows a record — home-screen stat headline, Receipts tab, Sharp Card record chip, tier-split badges — looks up its record from that map. Numbers never diverge across surfaces because they all come from the same aggregation. This was the fix for the "each surface shows a different record for the same picks" bug (2026-08-27, migration `20260827b_surface_records.sql`).

### 7.6 Current metrics (2026-09-08 snapshot, d30 window)

| Surface | Record | Units | Hit % |
|---------|--------|-------|-------|
| ALL sharp_card | 317-182-5 | +141.7u | 63.5% |
| ALL prop | 300-107 | +148.2u | 73.7% |
| ALL prop_prime | 177-41 | +100.6u | **81.2%** |
| ALL prop_strong | 123-66 | +28.8u | 65.1% |
| ALL potd | 17-7 | +7.7u | 70.8% |
| ALL mlb_sides | 81-51-3 | +22.6u | 61.4% |
| ALL ncaaf_sides | 34-15-2 | +15.9u | **69.4%** |
| ALL ladder | 8-12 | -6.3u | 40.0% |
| ALL dawg | 5-15 | -10.5u | 25.0% |

Ladder + Dawg are the underperformers — flagged for calibration review.

### 7.7 Where to look when a record looks wrong

| Symptom | Check |
|---------|-------|
| Record stale for one sport | `surface_records.updated_at` where sport=X — if stale, sport's grader step is failing |
| Record stale for one surface | Same table, filter by surface — one picker is broken |
| Sharp Card ALL doesn't equal sum of sports | `sharp_card_YYYY-MM-DD` in `jerry_cache` may be double-counting; check picker composition |
| PRIME/STRONG numbers different vs old bundled 'prop' | Expected — tier-split surfaces (prop_prime, prop_strong) added 2026-09-09 replace legacy 'prop' |
| Client shows blank record | `surface_records` fetch failed in `fetchSurfaceRecords()` OR (sport,surface,window) key missing — probably new sport not yet in aggregate |
| A specific game's pick shows ungraded but game finished | `jerry_reads.result IS NULL` — check `grade_jerry_reads.py --sport X` in latest workflow run |

**Adding a new surface** — three steps:
1. Write `pick_<surface>() -> list[dict]` returning `[{sport, date, result, stake, payout}, ...]`
2. Add to `SURFACES` dict at line 710
3. Ensure client references the new key: `surfaceRecords['ALL|<surface>|d30']`

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
| 2026-09-08 | pipeline map | Section 5 filled in: full workflow inventory (19 files), trigger chain, concurrency + heartbeat pattern, symptom → check-here debug table. |
| 2026-09-08 | grading + surfaces | Section 7 filled in: 4-stage flow (resolve → grade → compose → aggregate), 19-surface registry, client read pattern, current metrics snapshot, symptom → check debug table. |
| 2026-09-08 | GOAT NFL | Section 3.6 added: fused-signal composite shipped shadow-only. Composite formula, chip payload, backend-driven `chips_extra` pattern, promotion path documented. |
