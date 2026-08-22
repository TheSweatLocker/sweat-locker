# Sweat Locker Signal Framework — Universal Prop/Game Evaluation

**Established 2026-08-21 (user directive).**

Every prop, side, or total that the Sweat Locker system rates MUST be
evaluated against a comprehensive checklist of factors. Missing a factor
is a scoring bug — a signal_sources gap that needs to be filled, not an
acceptable simplification.

The specific factors differ by sport but the philosophy is universal:
**anything that can affect the outcome should be evaluated.**

---

## MLB — Pitcher Props Checklist (Ks / HA / BB / ER / outs)

| # | Factor | Signal example |
|---|---|---|
| 1 | Pitcher L3 form (ERA + K%) | `pitcher_recent_hot`, `pitcher_recent_k_hot` |
| 2 | Pitcher season xERA / sIERA | `pitcher_xera_high`, `pitcher_xera_elite` |
| 3 | Pitcher home/road split | `pitcher_home_road_split_penalty` |
| 4 | Pitcher 1st inning ERA | `pitcher_slow_start_1st_inn` |
| 5 | Days rest + last-start IP | ⚠️ ctx gap (need last_start_ip) |
| 6 | Career BAA vs this team | `pitcher_vs_team_career_baa_high/low` |
| 7 | Career ERA vs this team | `pitcher_vs_team_career_er_high/low` |
| 8 | Career K/9 vs this team | `pitcher_vs_team_career_k9_high/low` |
| 9 | Career avg IP vs this team (for outs) | `pitcher_vs_team_career_outs_short` |
| 10 | Recent (2+) starts vs team | `pitcher_vs_team_recent_hit_hard/dominant`, `_recent_baa_*` |
| 11 | Opp lineup L14 form (wRC+ proxy) | `opp_lineup_hot_wrc`, `opp_lineup_cold_wrc` |
| 12 | Opp lineup on heater/frozen | `opp_lineup_on_heater_l14`, `opp_lineup_frozen_l14` |
| 13 | Opp team K% (contact vs whiff) | `opp_lineup_k_heavy` |
| 14 | Opp BABIP L14 regression | `opp_babip_regression_flag` |
| 15 | Opp barrel% recent | `opp_barrel_pct_hot` |
| 16 | Opp team ATS L10 | `opp_ats_l10_hot/cold` |
| 17 | Own bullpen availability | `own_bullpen_gassed`, `own_bullpen_burned_3d` |
| 18 | Park hitter/pitcher factor | `pitcher_prop_park_hitter/pitcher` |
| 19 | Weather (wind direction + speed) | `pitcher_prop_wind_out/in` |
| 20 | Umpire zone tendency | ⚠️ gap (need ump ctx per game) |
| 21 | Team platoon vs opp hand | `home/away_platoon_edge/disadvantage` |
| 22 | Batter handedness vs pitcher | ⚠️ pitcher_throws NULL gap |
| 23 | Line movement / sharp split | `sharp_split_confirmed/triple`, `oddscrowd_*_fade_boost` |
| 24 | Model projection sanity check | `pitcher_projection_contradicts_over/under` |
| 25 | Legacy prop signals (l5, whiff, xera bands, etc) | `pitcher_l5_confirm`, `pitcher_last7_control` |

---

## MLB — Batter Props Checklist (hits / HR / RBI / bases)

1. Batter L7/L14 form (hot/cold)
2. Batter vs THIS pitcher career (BvP)
3. Batter vs LHP/RHP splits (blocked: pitcher_throws NULL)
4. Batter home/road split
5. Batter lineup spot (top of order = more PAs)
6. Team offense L14 form
7. Team offense vs opp hand
8. Opp pitcher xERA + L3 form
9. Opp bullpen strength (if starter goes short)
10. Park hitter-friendly?
11. Weather (wind out helps HRs)
12. Umpire zone
13. Rest days for batter
14. Legacy prop signals (l7_hot, batter_l14_heat, etc)

---

## MLB — Sides/Totals Checklist (ML / RL / Total)

1. Both starters full form + splits
2. Both bullpens
3. Both offenses L14
4. Both teams ATS L10 + season
5. Both teams ML L10
6. Home/road records
7. H2H recent
8. Public/sharp split
9. Line movement
10. Weather
11. Park factor
12. Rest advantage
13. Sharp scenario patterns
14. Model predictions (V4, Panel, Jerry, MC)
15. External handicapper picks
16. Confluence net

---

## NFL — Sides / Totals Checklist (18 factors)

| # | Factor | Signal example |
|---|---|---|
| 1 | Team ATS L10 (home + away) | `home/away_ats_hot/cold*` |
| 2 | Season ATS trends | `*_team_ats_hot/cold_season` |
| 3 | Home/road split (ML + ATS) | `home_ml_hot_at_home`, `away_ml_hot_on_road` |
| 4 | Rest advantage (home + away) | `nfl_home/away_rest_edge_2plus` |
| 5 | Short-week fatigue | `nfl_short_week_thursday_fade` |
| 6 | Division game (dogs cover ~53%) | `nfl_division_game`, `nfl_division_underdog_cover` |
| 7 | Weather — wind | `nfl_high_wind_under`, `nfl_extreme_wind_under` |
| 8 | Weather — cold | `nfl_cold_under`, `nfl_freezing_cold_under` |
| 9 | Dome game | `nfl_dome_over` |
| 10 | Offense mismatch | `nfl_offense_mismatch_home/away_edge` |
| 11 | Both offenses hot | `nfl_both_offenses_hot_over` |
| 12 | Model consensus (V4 + Panel) | `nfl_model_consensus_home/away_spread` |
| 13 | Projection sanity guard | `nfl_projection_contradicts_total_over/under` |
| 14 | Public split — ML/RL/total | `oddscrowd_ml/rl/total_fade_boost_nfl` |
| 15 | Sharp scenario match | `sharp_scenario_match_nfl` |
| 16 | Sharp split confirmed | `sharp_split_confirmed_nfl` |
| 17 | H2H recent trends | `h2h_*_dominant`, `h2h_over/under_streak` |
| 18 | External handicapper picks | `external_handicapper_pick_nfl` |

**Data gaps (backlog):**
- QB career vs specific defense (migration + backfill script shipped 8/21, awaiting apply)
- RB YPC vs run defense
- Receiver vs coverage type
- Divisional rivalry intensity beyond binary flag
- Ref crew tendencies

## NFL — Player Props Checklist (10 factors)

| # | Factor | Signal example |
|---|---|---|
| 1 | Player L10 hit count (extreme 8+/2-) | `nfl_prop_l10_extreme_extreme` |
| 2 | L5 hot / cold streak | `nfl_prop_l5_hot/cold_streak` |
| 3 | Season hit % consistency | `nfl_prop_season_hit_pct_high` |
| 4 | Projection edge (supports/opposes/strong) | `nfl_prop_projection_edge_*` / `_strong` |
| 5 | Weather — wind suppresses passing | `nfl_prop_wind_suppresses_pass` |
| 6 | Weather — cold boosts rushing | `nfl_prop_freezing_rush_boost` |
| 7 | Dome game — passing boost | `nfl_prop_dome_pass_boost` |
| 8 | QB career vs defense (blocked — needs backfill) | ⚠️ pending nfl_qb_vs_team backfill |
| 9 | Opp defense strength vs prop type | ⚠️ needs nfl_team_defense join |
| 10 | Lineup / injury status | ⚠️ needs nfl_injuries_pull integration |

## NCAAF — Sides / Totals Checklist (18 factors)

Mirror of NFL 18-factor checklist adapted for college:
- Signals: direct OC (ml/rl/total), extreme weather (wind/cold), SP+ home/away dominant, SP+ off-vs-D gap over, dual-D under, projection sanity, returning-production mismatch, home dog bark cover.

## NCAAF — Player Props Checklist (10 factors)

Same 10-factor structure as NFL props:
- Signals: L10 extreme, L5 hot/cold, projection edge (supports/opposes/strong), season hit%, wind suppresses pass, freezing rush boost, consensus edge, dome (not applicable, indoor stadiums rare in CFB).

## NBA — Sides / Totals Checklist (12 factors)

| # | Factor | Signal example |
|---|---|---|
| 1 | Direct oddscrowd (ml + total) | `oddscrowd_ml/total_fade_boost_nba` |
| 2 | Back-to-back fatigue (both sides) | `nba_home/away_b2b_fade` |
| 3 | Offense vs Defense rating mismatch | `nba_offense_mismatch_home/away` |
| 4 | Pace mismatch → total | `nba_pace_both_fast_over` / `_slow_under` |
| 5 | Star player OUT | `nba_home/away_star_out_fade` |
| 6 | Projection sanity guards | `nba_projection_contradicts_total_over/under` |
| 7 | Team ATS L10 | ⚠️ needs season data |
| 8 | Home/road splits | ⚠️ needs season data |
| 9 | H2H recent (small sample in NBA) | ⚠️ backlog |
| 10 | Public/sharp scenario matching | ⚠️ scenario handler needs NBA data |
| 11 | Model consensus (V4 + panel) | ⚠️ NBA V4 model TBD |
| 12 | External handicapper picks | Existing pattern |

## NBA — Player Props Checklist (12 factors)

| # | Factor | Signal example |
|---|---|---|
| 1 | L10 hit count extreme | `nba_prop_l10_extreme` |
| 2 | L5 hot/cold streak | `nba_prop_l5_hot/cold` |
| 3 | Season hit % consistency | `nba_prop_season_hit_pct_high` |
| 4 | Projection edge (supports/opposes/strong) | `nba_prop_projection_*` |
| 5 | Back-to-back fatigue | `nba_prop_b2b_fade` |
| 6 | Pace mismatch (high → over) | `nba_prop_high/low_pace_over/under` |
| 7 | Star OUT teammate boost | `nba_prop_star_out_teammate_boost` |
| 8 | Minutes projection sanity | `nba_prop_minutes_low_fade` |
| 9 | Player vs team scoring history | ⚠️ needs `nba_player_vs_team` backfill |
| 10 | Matchup at position | ⚠️ needs positional-def data |
| 11 | Home/road split | ⚠️ needs season data |
| 12 | Rest days | ⚠️ needs rest calc |

## NHL — Player Props Checklist (10 factors)

| # | Factor | Signal example |
|---|---|---|
| 1 | L10 hit count extreme | `nhl_prop_l10_extreme` |
| 2 | L5 hot/cold streak | `nhl_prop_l5_hot/cold` |
| 3 | Season hit % consistency | `nhl_prop_season_hit_pct_high` |
| 4 | Projection edge (supports/opposes) | `nhl_prop_projection_*` |
| 5 | Facing elite goalie (SV%>=.920) | `nhl_prop_facing_elite_goalie` |
| 6 | Facing weak goalie (SV%<=.895) | `nhl_prop_facing_weak_goalie` |
| 7 | Back-to-back fatigue | `nhl_prop_b2b_fade` |
| 8 | PP opportunity vs weak PK | `nhl_prop_pp_specialist_hot_pk` |
| 9 | Home/road split | ⚠️ needs season data |
| 10 | Player vs opponent scoring history | ⚠️ needs `nhl_player_vs_team` backfill |

## NHL — Sides / Totals Checklist (12 factors)

| # | Factor | Signal example |
|---|---|---|
| 1 | Direct oddscrowd (ml + puckline + total) | `oddscrowd_ml/puckline/total_fade_boost_nhl` |
| 2 | Back-to-back fatigue (both sides) | `nhl_home/away_b2b_fade` |
| 3 | Elite starting goalie (SV% >= .920) | `nhl_home/away_goalie_elite` |
| 4 | Dual elite goalies → under | `nhl_both_goalies_elite_under` |
| 5 | Power play vs weak penalty kill | `nhl_home/away_pp_vs_weak_pk` |
| 6 | Projection sanity total over/under | `nhl_projection_contradicts_total_over/under` |
| 7 | Team ATS home/road | ⚠️ needs season data |
| 8 | Team form L10 (5-4-1 = normalize) | ⚠️ needs season data |
| 9 | H2H recent | ⚠️ needs backfill |
| 10 | Model consensus (once NHL V4 exists) | ⚠️ pending |
| 11 | External handicapper picks | Existing pattern |
| 12 | Sharp scenario matching | ⚠️ scenario handler needs NHL data |

## NCAAB — Sides / Totals Checklist (12 factors)

| # | Factor | Signal example |
|---|---|---|
| 1 | Direct oddscrowd | `oddscrowd_ml/spread/total_fade_boost_ncaab` |
| 2 | Efficiency model dominant (home/away) | `ncaab_efficiency_home/away_dominant` |
| 3 | Pace mismatch (both fast → over) | `ncaab_pace_both_fast_over` / `_slow_under` |
| 4 | 3PT shooting vs def gap | `ncaab_3pt_shooting_gap_home` |
| 5 | Home court advantage (strong home record) | `ncaab_home_court_advantage` |
| 6 | Conference game dog cover | `ncaab_conference_dog_cover` |
| 7 | Projection sanity | `ncaab_projection_contradicts_total_*` |
| 8 | Team form L10 | ⚠️ scoped season data |
| 9 | KenPom / SP+ style ratings | Use "efficiency model" branding |
| 10 | H2H recent (small NCAAB sample) | ⚠️ backlog |
| 11 | External handicapper picks | Existing pattern |
| 12 | Sharp scenario matching | ⚠️ scenario handler needs NCAAB data |

## NFL — QB vs Defense Career Signals (6, pending wire-up)

`nfl_qb_vs_team` table populated 8/21 (1425 rows across 2021-2025 via nflverse). Signal definitions shipped:
- `nfl_qb_owns_defense_career` (career QB rating >= 100 vs opp, 3+ starts)
- `nfl_qb_owned_by_defense_career` (rating <= 75)
- `nfl_qb_high/low_yds_vs_team` (recent avg vs prop line ±30 yds)
- `nfl_qb_int_prone_vs_team` (recent 1.5+ INT avg)
- `nfl_qb_td_prolific_vs_team` (recent 2+ TD avg)

**Follow-up wire-up needed:** either (a) join `qb_vs_team_*` fields onto `nfl_game_context` at build time, OR (b) add `_handler_nfl_qb` in ensemble_scorer.py. Signals are defined but won't fire until one of these ships.

## Cross-Sport Philosophy

**Same principle every sport:** every factor that a smart bettor would ask should have a signal firing (or a documented ctx gap noted as backlog). The MLB 25-factor pitcher prop checklist is the template — adapt per sport with the specific factors that matter for that game.

---

## Enforcement — Coverage Audit

Run `python coverage_audit.py --date YYYY-MM-DD` after any score run.

For each prop/game evaluated, reports:
  - N factors checked / M factors possible
  - Which specific factors returned no signal (missing data or too-strict thresholds)
  - Any prop with < 12/25 factors evaluated = FLAG for investigation

**Any prop with score-driving contribution from < 4 distinct classes = also flagged** (single-class dominance = fragile).

---

## Signal Development Discipline

When shipping a new signal:
1. Which of the 25 factor slots is it filling?
2. Is there a MIRROR signal for the opposite direction? (learn from platoon/RL bias bugs)
3. Is the threshold tuned? (learn from Luzardo .268 vs .270 miss)
4. Does the strength respect the "verifier vs primary" tiering?
5. Update this doc.

When editing an existing signal:
1. Check for cross-sport contamination in registry
2. Test with `_ml_deep_dive.py` before and after
3. Note the impact on today's slate

**Never let a signal ship without a mirror if the opposite direction is meaningful.**
