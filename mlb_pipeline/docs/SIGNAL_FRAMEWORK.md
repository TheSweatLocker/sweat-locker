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

## Cross-Sport Philosophy

**NFL:** QB vs specific defense history, offense vs D scheme, RB vs run defense YPC, receiver vs coverage type, home/road, weather (esp cold/wind), rest, division rival intensity.

**NBA:** Player vs team scoring history, pace mismatch, offensive/defensive ratings, back-to-back fatigue, star player status, matchup at position.

**NHL:** Goalie vs opponent shots-against, PP/PK matchup, back-to-back, home ice.

**Same principle every sport:** every factor that a smart bettor would ask should have a signal firing (or a documented ctx gap noted as backlog).

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
