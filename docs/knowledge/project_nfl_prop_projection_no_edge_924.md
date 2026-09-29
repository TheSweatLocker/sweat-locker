---
name: project_nfl_prop_projection_no_edge_924
description: "RETRACTED 58.3% claim — NFL prop projection measures 50.9%, below the -110 breakeven; five of seven NFL prop signals now weight 0"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-24T21:26:24.206Z
---

**2026-09-24.** I repeatedly told Andy the NFL prop projection was "the
only measured win on the board" at **58.3% vs 50.8% live**. That figure was
wrong and is retracted. It is not reproducible under any framing of the
data.

Measured on 1,331 graded NFL props (2026-09-09..09-21) once
`nfl_pipeline_props.projection` was finally persisted:

- all: 677-654 = **50.9%**
- edge <15%: 49.4% (n=532) · edge >=15%: **51.8%** (n=799) · edge >=30%: **51.8%** (n=413)
- breakeven at -110 is **52.4%** — the best bucket is 0.6pp *under* it
- **no monotonicity**: 6-10% 46.5%, 10-15% 52.6%, 15-20% 55.5%, 20-30% 49.4%, 30-40% 49.3%, 40%+ 53.0%

A 30%+ edge performs identically to 15%+, which is exactly the claim
`nfl_prop_projection_strong` makes at strength 0.75.

The charitable reading — that I'd measured the projection's raw directional
skill rather than graded results — gives the *same* number (677/1331),
because the generator refuses wrong-side picks so the published side always
equals the projection's side.

`backfill_prop_signal_tiers.py --sport NFL` reproduces this independently
and `edge_weight` zeroed the signals on evidence:

    nfl_prop_projection_edge_supports  51.8%  n=799  -> 0.000
    nfl_prop_projection_strong         51.8%  n=413  -> 0.000
    nfl_prop_l5_hot_streak             51.6%  n=409  -> 0.000
    nfl_prop_l10_extreme_extreme       44.3%  n=221  -> 0.000 ANTI
    nfl_prop_season_hit_pct_high       36.9%  n=149  -> 0.000 ANTI
    nfl_prop_l5_cold_streak            54.7%  n=117  -> 0.318

**Why:** NFL props as currently constructed have no demonstrated edge. The
projection blend (0.60 L4 + 0.35 season + 0.05 baseline, or the
fantasy-weighted variant) is not where the value is. The signals that do
earn weight are elsewhere: `weather_calm` 67.4% (n=46), `game_script_pass`
59.3% (n=54), `implied_low` 57.0% (n=79), `def_top10` 56.5% (n=209).

**How to apply:** Never cite 58.3% again. Do not propose weighting the
projection edge without new evidence. Build on the weather/game-script/
implied-total signals instead. Related: published tier does not order NFL
prop outcomes (LEAN 55.3% n=459 > STRONG 50.9% n=334 > LIGHT 46.8% n=299),
so the tier label is miscommunicating — see [[project_prop_tier_ux_confusion_720]].
Persisting the projection was still correct as a data-integrity fix; see
[[feedback_fix_at_root_three_parts]] and [[feedback_sample_size_with_pct]].
