---
name: juice-fav-rl-trap-724
description: "45-day MLB data confirms heavy home favs cover -1.5 only 29% (n=24), light home favs (-130 to -149) only 39.7% (n=121). RL -1.5 on juiced favs is a systematic trap even when ML is a value."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-24T23:38:59.140Z
---

**Set 2026-07-24 evening after user (Andy) noticed Brewers -1.5 loss pattern.**

## The pattern

45-day audit of mlb_game_results (n=522 games with home_ml):

| Home ML bucket | n | ML hit % | **-1.5 RL cover %** |
|---|---|---|---|
| -200 or heavier (heavy fav) | 25 | 64.0% | **29.2%** 🚨 |
| -150 to -199 (mod fav) | 78 | 62.8% | 53.8% |
| -130 to -149 (light fav) | 123 | 54.5% | **39.7%** 🚨 |
| -129 to -110 (pickem) | 121 | 50.4% | 53.3% |

**ML hit rates match implied probability** — favorites do win at expected rate.
**RL -1.5 systematically UNDER-covers** in the -200+ and -130-to-149 bands.

## Mechanism (known MLB dynamic)

Heavy favorites win 60-64% of the time as expected, but MLB compresses
scoring in the 8th/9th so ~40% of games decided by 1 run. Result: fav
wins ML but only covers -1.5 in ~30-40% of situations.

## Trigger case

7/24 Brewers -1.5 as heavy home fav (ML likely -240ish). MC HIGH-CONF
chip fired at 86.5% to WIN. Bet was on -1.5 RL. Rockies won straight
up 5-2. Even the ML would've lost this specific game, but the
architectural mistake was that MC's 86.5% signal was ML-strength, not
RL-strength.

## Fix required (queue)

1. **compute_primary_play**: when MC HIGH-CONF fires on home fav with
   ML ≤ -150, surface ML as the primary play, not RL -1.5.
2. **Add trap tag**: any RL -1.5 pick on home fav priced -130 or worse
   gets a "juice-fav RL trap" downgrade note in primary_play.audit_note.
3. **Broader**: the play_of_day tier gate should apply this asymmetry
   — MC HIGH-CONF at 80%+ is a ML-strength signal, RL cover is a
   different distribution.

## Related

- [[project_spread_delta_trap_zone]] — 6/24 audit: spread_delta 1.5-2.0
  = 40-43% trap zone. This finding extends: it's specifically the
  RL that traps, not the ML.
- [[project_june5_cohort_audit]] — 6/5 audit: fav -130/-150 = trap
- [[feedback_verify_ml_direction]] — verify convention before framing

## Followup query for reproducibility

  SELECT
    CASE
      WHEN home_ml_open <= -200 THEN 'heavy_home_fav'
      WHEN home_ml_open <= -150 THEN 'mod_home_fav'
      WHEN home_ml_open <= -130 THEN 'light_home_fav'
      WHEN home_ml_open <= -110 THEN 'pickem_home'
    END AS bucket,
    COUNT(*) AS n,
    SUM(CASE WHEN home_win THEN 1 ELSE 0 END)::float / COUNT(*) AS ml_rate,
    SUM(CASE WHEN spread_result='home_covered' THEN 1 ELSE 0 END)::float
      / NULLIF(SUM(CASE WHEN spread_result != 'push' THEN 1 ELSE 0 END), 0) AS ats_rate
  FROM mlb_game_results
  WHERE game_date >= '2026-06-09' AND home_ml_open IS NOT NULL AND home_score IS NOT NULL
  GROUP BY 1;
