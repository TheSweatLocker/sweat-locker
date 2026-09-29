---
name: project_nfl_prop_scoring_diagnosis_924
description: "NFL prop scoring root cause 9/24: probabilities overconfident (said 93% got 59%), no price gate existed, real breakeven is 54.2% not 52.4%, edge is in counting stats not yardage"
metadata:
  type: project
---

**2026-09-24.** Full diagnosis of why NFL prop scoring does not work. Not a
projection-quality problem.

## The projection is overconfident and knows least where it claims most

Turning the projection into P(clear) — Poisson for counting families, normal
with the player's own dispersion for yardage — against outcomes:

    said 53.0%  got 50.7%   -2.3pp   n=146
    said 57.8%  got 53.5%   -4.3pp   n=226
    said 64.6%  got 52.5%  -12.1pp   n=419
    said 75.8%  got 53.8%  -22.0pp   n=240
    said 92.6%  got 58.7%  -33.9pp   n=75

Claimed 53-93%, delivered 51-59%. **This single fact explains the tier ladder
ranking backwards, edge_pct buckets being non-monotonic, and every projection
signal measuring below breakeven.**

The ranking is weakly right though (actual rises 50.7 -> 58.7), so a
calibration map helps: fit on early dates, applied to later, mean gap
-14.9pp -> **-5.4pp OOS**. Betting only where calibrated P beats the price
paid: margin 0pp -4.82%, 2pp -2.86%, **4pp +3.73% (n=107)**.

Ceiling is FEATURES not math — the fitted map collapsed to two levels (49.6%,
56.3%). A better L4/season blend cannot fix this.

## Price was never considered at all

**Real breakeven is 54.2%, not 52.4%** — average published price -118.6.
NFL props had NO price gate at any layer (the -300..+150 band is MLB-only);
they shipped at -276 and +190.

    worse than -200   59.5% (needs 69.8%)  ROI -14.58%  n=37
    -200 to -150      56.7% (needs 62.7%)  ROI  -9.54%  n=150
    -150 to -130      63.7% (needs 58.4%)  ROI  +8.96%  n=113
    -130 to -115      48.4% (needs 54.7%)  ROI -11.64%  n=215
    -115 to -101      49.6% (needs 52.7%)  ROI  -5.72%  n=645
    plus money        43.3% (needs 46.1%)  ROI  -6.03%  n=171

Shipped a -150..+150 source gate (`NFL_PROP_ODDS_MIN/MAX`, env-overridable).

## Where the edge actually is

- **Counting stats 53.8% (n=732) vs yardage 47.2% (n=599).** pass_yds 41.8%,
  rush_yds 43.9% vs pass_attempts 56.8%, rush_attempts 56.9%. Role/usage is
  re-priced slowly; explosive plays are unforecastable.
- **Hot players lose on BOTH sides** (L10>=8: over -20.3% n=79, under -11.1%
  n=113) — line over-shaded, no edge either way. Best readable segment is
  mid-form unders (L10 3-5 under 57.5%, +6.17%, n=160).
- Positive situational signals: `weather_calm` 70.7% (n=41),
  `game_script_pass` 57.8% (n=64), `implied_low` 57.0% (n=79),
  `def_top10` 56.2% (n=209).
- Our published **conviction does not predict profit**: 0-59 -12.0%,
  60-69 +2.2%, 70-79 -10.0%, 80-100 -0.5%.

**Why:** the moat has to come from situational/usage features plus price
discipline, not from out-projecting a player's own stat line — the market
already prices form well.

**How to apply:** score in probability space against a de-vigged fair price
(both sides now captured as of 9/24; cannot be backtested, shadow forward).
Never quote 52.4% as our breakeven. Prefer counting stats. See
[[project_nfl_prop_projection_no_edge_924]] and
[[feedback_sample_size_with_pct]].
