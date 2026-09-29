---
name: cohort-engine-universal-architecture
description: "Cohort engine = the analytical moat across sports. Universal architecture (shrinkage, recency, tiers, refresh) with sport-specific feature modules. Drafted 2026-06-09."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

The cohort engine is THE advanced model portion of the app — the analytical moat that differentiates The Sweat Locker from pick services and basic odds aggregators. It's the engine that says "this pattern hit 73% over 64 games historically" instead of "we like this pick."

**Founder confirmed 2026-06-09 evening:** Universal architecture, sport-specific cohorts, this IS the advanced model layer.

## Universal layer (sport-agnostic, already built)

- `refresh_cohort_signals.py` — takes a `get_features_fn` parameter; sport-agnostic
- Bayesian shrinkage math (shrink raw_pct toward baseline using prior_n=30)
- Recency veto (30d hit rate must be within RECENCY_VETO_DROP_PP of lifetime)
- Tier system: LOCK ≥75% / STRONG_EDGE ≥65% / LEAN ≥60% / NEUTRAL 45-60 / SOFT_FADE ≥35% / FADE ≥28% / HARD_FADE <28%
- Conviction delta system: +18 / +10 / +4 / 0 / -5 / -12 / -25
- Min sample threshold (MIN_RAW_N=10, can be per-sport tuned)
- jerry_cache storage row 'cohort_signals' (one per sport — keyed by sport prefix)
- Wire-in pattern for POTD/DAWG/sweat card conviction adjustments (Phase 1+2)
- Calibration loop (cohort_calibration_report.py) for nightly self-grading

## Sport-specific layer (one per sport)

Each sport gets:
1. `cohort_features_<sport>.py` — feature extraction module
2. Sport-specific play types (e.g., MLB has v3_ml/v3_tot/v3_rl; NFL would have v3_spread/v3_total/v3_ml; NBA similar to NFL with total/spread/ML)
3. Sport-specific game results table (mlb_game_results, nfl_game_results, etc.)
4. Sport-specific baseline calibration

## Cross-sport universal features (factor into shared module)

Some signals translate across every sport — promote to a shared `cohort_features_universal.py` that every sport's module imports:

- **Line movement / sharp money** — `open_spread` vs `close_spread` magnitude + direction
- **Rest days** — `home_days_rest`, `away_days_rest` (especially powerful for NBA/NFL)
- **Travel / consecutive road** — `away_consecutive_road_games`, distance traveled
- **Defense gap concept** — MLB OAA, NFL DVOA defense, NBA defensive rating — same idea, different field names
- **Bullpen / depth fatigue** — MLB bp_relievers_3d, NBA B2B, NFL injury report severity
- **Public vs sharp split** (when available) — betting % vs handle %
- **Returning from road trip** — `home_returning_from_road` (universal)

The OAA finding tonight (8 of 10 top new MLB ML cohorts involved OAA) suggests defensive-efficiency-gap is a universal blind spot across the engine. Apply the same pattern in NFL/NBA from day one.

## Storage convention

`jerry_cache` rows keyed by sport prefix:
- `cohort_signals` → MLB (legacy default — keep for back-compat)
- `nfl_cohort_signals` → NFL
- `nba_cohort_signals` → NBA
- `ncaab_cohort_signals` → NCAAB

`cohort_lookup.py` would accept an optional `sport='mlb'` parameter to read the right row.

## Per-sport feature design seed list

### NFL (Phase 2 launch August)
- EPA differential (offense + defense)
- DVOA gap (offense + defense)
- 4th-quarter point differential L4 (close-game performance)
- Pass-rush vs O-line matchup
- Travel distance (especially for west-coast teams)
- Weather (outdoor stadiums, wind, cold)
- Public bias (heavy-public team in primetime)
- QB rest (5+ days = full prep)
- Sharp money line movement
- Coaching matchup (some coaches blank specific opponents)
- Divisional vs non-divisional (familiarity affects spread)

### NBA (already partial via nba_game_picks)
- Pace gap
- eFG% offense vs eFG% defense
- B2B games
- Rest gap (one team rested, one tired)
- Travel distance / time zone change
- Star availability
- Public bias
- Garbage time risk (blowout-prone matchups)
- 3PA volume vs opponent 3PT defense

### NCAAB — STRONGEST data fit of any sport

**Why NCAAB is the showcase sport for the cohort engine:**
- ~5,800 games/season × multiple historical seasons = ~29,000 lifetime sample. Most cohorts will land n=100-500 — that's an order of magnitude above MLB. shrunken_pct stays close to raw_pct because the prior pull is negligible at high n. Rules surface with real statistical conviction, not bayesian-smoothed lean.
- KenPom data is already structured cohort gold (AdjOE/AdjDE/pace/four factors). No feature engineering required for the math layer.
- `_backtest_ncaab_2025.py` + `_bart_2025_games.csv` + `_bart_2025_teams.csv` files already in repo — backtest infrastructure half-built.
- Per project_ncaab_scope: spreads/totals/ML only (no player props) — that's exactly the game-level work the cohort engine handles natively.

**Seed feature list (queue for v1.0 implementation):**

KenPom-based:
- `kenpom_gap_huge` (rank gap ≥30 — talent disparity)
- `kenpom_gap_loud` (rank gap ≥15)
- `adjoe_gap_loud` (offensive efficiency gap ≥10pt)
- `adjde_gap_loud` (defensive efficiency gap ≥10pt)
- `efg_gap_loud` (eFG% gap material)
- `to_gap_loud` (turnover rate gap material)
- `oreb_gap_loud` (offensive rebound gap material)
- `ft_rate_gap_loud` (FT rate gap material)
- `tempo_gap_fast_meets_slow` (slow team usually controls pace)
- `luck_loud_one_side` (KenPom luck rating extreme)
- `sos_adjusted_dog_strong` (dog has strong SOS but weak record)

Travel / scheduling:
- `road_in_road_trip` (3rd+ game in road sequence)
- `road_long_travel` (700+ miles)
- `road_altitude_change` (sea level → mile high or reverse)
- `back_to_back_road_dog` (rare but huge edge when surfaces)

Conference:
- `power_conference_mid_season` (Big 6 league play)
- `mid_major_vs_power` (matchup spread profile)
- `conf_familiarity` (Nth time playing this opponent — defenses adapt)
- `divisional_familiarity` (rematches go UNDER)
- `conf_tournament_low_seed` (underseeded mid-majors over-perform)
- `bubble_team_late_season` (motivation factor)

Officials / venue:
- `home_court_loud` (Cameron Crazies, Hinkle, etc. — top HCA venues)
- `ref_conference_specific` (different leagues call different foul profiles)
- `late_game_foul_lean` (refs swallow whistle in close-game endgames)

Public bias:
- `nationally_televised_public_team` (Duke, UK, UNC, etc. — public-bias adjustment)
- `line_moved_against_public` (sharp signal)

**Expected outcome at NCAAB launch:** the cohort engine surfaces with materially higher confidence per game than MLB does, because sample sizes are 10x higher. This is the sport where "73% historical pattern" gets to say "73% over 287 games" — that's casual-credibility gold.

**Implementation order:**
1. Build `cohort_features_ncaab.py` (factor cross-sport universals into shared module first)
2. Backfill historical KenPom data into `ncaab_game_results` table (multi-season)
3. Run `refresh_cohort_signals.py --sport ncaab` against the historical sample
4. Inspect emitted rules, validate against expected KP patterns
5. Wire into NCAAB game_context + scorer
6. Ship for 2026-27 NCAAB season (November launch window)

## Implementation phasing

**Now:** MLB Phase A live (shipped 2026-06-09, commit b72ae4b)
**Next 30d:** MLB Phase B (slate-day audit), MLB v2 features as season data grows
**Late July / early August:** NFL feature module design + initial cohort population
**August:** NFL Phase 1 cohort population from 2025 backfill data
**September:** NFL Phase 2 launch with cohort wire-in already battle-tested
**October:** NBA cohort engine seeded (if season starting)

## Architecture goal: zero code change across sports

Adding a new sport should require:
1. Build a `cohort_features_<sport>.py` with sport-specific feature extraction
2. Build a sport-specific play type list
3. Backfill historical data into `<sport>_game_results` table
4. Run `refresh_cohort_signals.py --sport <sport>`

That's it. No architectural changes. Tiers, shrinkage, recency, refresh cycle, jerry_cache write — all reused.

## Related
- [[project_ml_rl_cohort_expansion_spec]] — MLB Phase A expansion shipped 6/9
- [[project_attribution_backtest_608]] — original backtest framework that became the engine
- [[project_dynamic_cohort_framework_607]] — dynamic % labeling infrastructure (must extend to multi-sport)
- [[project_post_launch_roadmap_may_to_nfl]] — version cadence + NFL launch timing
- [[project_casual_bettor_ux_docket]] — cohort hit rate is the cross-sport casual-credibility surface
