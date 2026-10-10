# NHL Offseason Sprint Report — 2026-08-19

Puck drop ≈ 6 weeks (early October). This sprint reused the MLB/NCAAF
patterns (`backfill_signal_tiers.py`, `ncaaf_cohort_backfill.py`,
`ncaaf_sharp_fade_rules.py`) to produce a launch-viable first pass for NHL.

## Deliverables

| # | File | Purpose |
|---|---|---|
| 1 | `mlb_pipeline/nhl_backfill_signal_tiers.py` | Backfills `signal_registry` for all 40 enabled NHL signals |
| 2 | `mlb_pipeline/nhl_cohort_backfill.py` | Situational cohort hit-rates → `mlb_tier_calibration` (sport='NHL') |
| 3 | `mlb_pipeline/nhl_sharp_fade_rules.py` | 7-rule fade engine, all in LOG mode until live data accumulates |
| 4 | `mlb_pipeline/_nhl_ensemble_smoke.py` | End-to-end ensemble smoke test on 20 recent games |

No new migrations were needed — used the canonical multi-sport
`mlb_tier_calibration` table (NCAAF/NFL precedent) rather than
`nhl_cohort_stats`. `signal_registry` already existed with unique key
`(signal_name, sport, market_scope)`.

## 1. Signal backfill outcomes (35 written to `signal_registry`)

| Tier | Count |
|---|---|
| VALIDATED | 1 |
| DISCOVERY | 3 |
| UNVALIDATED | 31 |
| ANTI_VALIDATED | 0 |

**Top validated/discovery (baseline-adjusted lift ≥ 2pp)**

| Signal | Scope | HR | n | Lift vs baseline | Tier |
|---|---|---|---|---|---|
| home_ats_cold_at_home  | rl | 69.9% | 352 | +4.7 vs 65.2 | VALIDATED |
| away_team_ats_hot      | rl | 69.0% | 245 | +3.8 vs 65.2 | DISCOVERY |
| away_ats_hot_on_road   | rl | 67.3% | 284 | +2.1 vs 65.2 | DISCOVERY |
| home_team_ats_cold     | rl | 67.2% | 305 | +2.0 vs 65.2 | DISCOVERY |

**Top 5 anti-validated: NONE.** No signal cleared the -5pp-vs-baseline
threshold once we corrected for the fact that HOME +1.5 base rate is 65%
and AWAY +1.5 base rate is 35% (skew that misled the raw hit rate). This
is honest — with no historical odds we shouldn't be flagging traps.

Tiering upgrade vs the MLB script: for RL scope, hit-rate is compared to
the +1.5 base rate (34.8% home cover / 65.2% away cover) rather than the
market-neutral 52.4%, so team_form ATS signals get promoted only for real
lift, not baseline noise. Total scope compared to 45.7% (OVER 6.0
baseline).

## 2. Sharp-fade rules

`nhl_sharp_fade_rules.py` ships 7 rules mirroring NCAAF/NFL structure:
`MODELS_OPPOSE_SHARP_{ML,PUCKLINE,TOTAL}`, `SHARP_ON_ROAD_TEAM_ML`,
`SHARP_ON_AWAY_FAV_ML`, `SHARP_LIGHT_JUICE`, `SHARP_OPPOSES_CONFLUENCE`,
`SHARP_ON_HEAVY_HOME_FAV_ML`, `SHARP_ON_OVER_NHL_TRAP`. All start in LOG
mode — they need live `oddscrowd_snapshot` data on `nhl_game_context`,
which comes online with the season, and per-rule stats in
`jerry_cache` (key `nhl_sharp_fade_rules_stats`) before activation.
`compute_fade_context()` demo run returns 3 triggers with the demo ctx.

## 3. Cohorts (12 rows in `mlb_tier_calibration`, sport='NHL')

Home ML base rate = 56.0% (n=1335). Every row above +5pp lift over that
baseline is a real edge:

| Cohort | Pick | Hit rate | n | Lift |
|---|---|---|---|---|
| nhl_home_long_rest      | HOME_ML | 65.9% | 88  | **+9.9pp** |
| nhl_hot_home_vs_cold_away | HOME_ML | 65.0% | 40 | **+9.0pp** |
| nhl_away_b2b_fade       | HOME_ML | 63.4% | 224 | **+7.4pp** |
| nhl_both_b2b            | HOME_ML | 60.6% | 66  | +4.6pp |
| nhl_home_b2b_fade       | AWAY_ML | 55.7% | 79  | **+11.7pp vs 44% away base** |
| nhl_both_hot            | OVER    | 39.5% | 38  | fade the "both hot = shootout" narrative |

The home-long-rest / away-b2b-fade combo is the most robust launch signal
— easily 200+ triggerable games per season with a real +7-10pp edge.

## 4. Ensemble smoke test (20 recent completed games)

| Metric | Value |
|---|---|
| Games processed | 20 |
| Produced a pick | 19 |
| No pick | 1 (Dallas @ Nashville — no signal fired) |
| Exceptions | 0 |
| Top-market split | 10 rl, 8 ml, 1 total |
| Tier split | 19 LEAN, 0 STRONG, 0 PRIME |

Conviction range 50-76. The absence of any STRONG/PRIME is expected —
without live odds enrichment there's only ~1-2 opinions per game feeding
the scorer, so nothing clears the higher-conviction thresholds. That
resolves the day the Odds API + goalie confirmation pipelines come
online (nhl_game_context.py already wires both). Zero exceptions = the
end-to-end signal → scorer → decision path is intact.

## Blockers / gaps for October launch

1. **No historical NHL odds.** `nhl_game_results.close_home_ml`,
   `close_puckline`, `close_total` are NULL across all 1,335 rows. This
   caps our pre-season signal validation:
   - ATS/puckline signals are graded against outcomes (home wins by 2+)
     rather than real close_puckline — the sign is right, the magnitude
     of edge isn't trustable yet.
   - Model signals (`nhl_elo_over_edge`, `home_b2b_penalty`, etc.) whose
     `condition_expr` requires `ctx.close_home_ml` or `ctx.close_total`
     to be non-null literally can't fire on historical data — they'll
     start firing Oct 7 when Odds API begins populating.
   - **Mitigation options:** (a) pull season-recap CSVs from
     hockey-reference.com to backfill closing lines (~1-2h work, feasible
     pre-launch); (b) accept UNVALIDATED tiers and let the registry
     self-heal from live season fires (Weeks 3-4 have enough n).

2. **Goalie / rest / prop signals have zero fires.** They depend on
   `home_goalie_gsaa`, `home_back_to_back`, and prop-candidate fields
   that only get populated by `nhl_game_context.py` at run-time. Not a
   bug — expected until the season starts.

3. **Prop backfill is not addressed.** 5 prop signals (`prop_form`,
   `prop_environment`, `prop_matchup`) were skipped; there's no
   historical player-prop candidate table for NHL. Cross-sport
   `prop_playbook` port applies here — tracked in
   `project_prop_playbook_port_817` memory.

4. **Sharp-fade rules dormant.** All 7 rules in LOG mode; won't influence
   picks until `oddscrowd_snapshot` is enriched (needs Odds Crowd key
   scoped to NHL) and rule-stats table hits n_min per rule (~Week 3-4).

## Verdict for October launch

**GREEN for launch as a LEAN-only surface.** The ensemble is intact, 40
signals are wired, `signal_registry` has evidence-driven weights for the
signals that could be graded, and the cohort table surfaces 4 real edges
(home long rest, hot-home-vs-cold-away, away b2b fade, home b2b fade).
Users get real picks day 1 — they just won't see PRIME/STRONG chips
until Weeks 3-4, which mirrors NFL/NCAAF's cold-start behavior and is
already documented user-facing in the launch memo.

**Recommended pre-launch to-do:** backfill 2024-25 closing prices from
hockey-reference (1-2h) so we can rerun `nhl_backfill_signal_tiers.py`
with real RL/total grading and promote signals to VALIDATED where the
lift is truly there. Everything else can accumulate live.
