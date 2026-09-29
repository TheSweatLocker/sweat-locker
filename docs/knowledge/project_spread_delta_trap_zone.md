---
name: project-spread-delta-trap-zone
description: Audit found spread_delta 1.5-2.0 is a trap zone (40-43% hit rate). STRONG ML threshold of |delta|≥1.5 in compute_primary_play and play_of_day sits inside the trap — should raise to ≥2.0
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Cohort audit (5/21) found a U-shape in `spread_delta` hit rates that exposes a threshold bug in the current PRIME/STRONG ML logic.

**The U-shape (lifetime data, n=240+ per bucket):**
- `spread_delta_lt1` (<1.0): 43-50% (coinflip / slight fade)
- `spread_delta_1_1_5` (1.0-1.5): 55-58% (sweet spot — small edge cashes)
- `spread_delta_1_5_2` (1.5-2.0): **40-43% (TRAP — model's pick LOSES)** ⚠️
- `spread_delta_ge2` (≥2.0): 55-58% (real conviction returns)

**Why the bug:** Current STRONG ML branch in `compute_primary_play` (game_context.py) and `play_of_day._rl_alt_for_juiced_chalk` requires `|spread_delta| ≥ 1.5`. That puts STRONG picks square in the trap zone (1.5-2.0 = 40% hit rate). PRIME ML requires `≥2.0` which is fine.

**Why:** Mid-magnitude disagreement (1.5-2.0) looks like edge but is mostly noise + model overconfidence. Strong disagreement (≥2.0) is genuine signal; small disagreement (<1.5) is properly coinflip-priced.

**How to apply (post-revisit):**
1. Raise STRONG ML threshold in `compute_primary_play` from `abs_delta ≥ 1.5` to `≥ 2.0` (or split: STRONG = 2.0-2.5, PRIME = 2.5+)
2. Same fix in `play_of_day._rl_alt_for_juiced_chalk` (line ~592: `if cn < 4 or abs(ps) < 1.5: return None` — raise to `< 2.0`)
3. Drop `spread_delta_strong_1_5_2` cohort from POTD audit-driven selector (it's below break-even)
4. Add positive cohorts to surface: `autofade_dog_high_conv` (58-65%), `autofade_chalk_high_mag` (56-65%), `wrc_diff_away_adv_ml` (58% n=130+)

**Other useful cohort discoveries (same audit):**
- `autofade_dog_high_conv`: 58-65% — when model picks DOG with high conviction, dog cashes
- `autofade_chalk_high_mag`: 56-65% — fading chalk in high-magnitude games
- `wrc_diff_away_adv_ml`: 58% n=130+ — away team with wRC+ advantage = under-priced
- `bp_diff_away_adv_ml`: 42-44% — counter-intuitive fade: away BP advantage loses

**Caveat:** The U-shape was found on v3 `spread_delta` data. v4 (XGBoost) has only ~5 weeks of resolved picks — not enough sample to confirm v4 has the same U-shape. Do NOT raise the v4-based threshold without separate audit. The fix above applies to v3-derived spread_delta only.

Related: [[project_auto_fade_calibration]] — cohort-based suppress/flip gate work. This finding gives concrete cohort hit rates to wire into auto_fade.py.
Related: [[feedback_verify_ml_direction]] — Always verify ML/spread direction before framing. This bug is a sibling: the threshold itself was mis-calibrated, not just the direction.
