---
name: project_ncaaf_duplicate_total_lens_920
description: NCAAF sp_plus_pred_total is a byte-identical alias of projected_total - never count it as a second model
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T01:01:25.811Z
---

`sp_plus_pred_total` == `projected_total`, byte for byte. Both are
assigned the same `round(total, 2)` in
`ncaaf_game_context.compute_projections` (~line 751). Verified 72/72
identical, mean |diff| 0.00. It is a DISPLAY ALIAS added 2026-09-16 to
fill the NumbersPanel SP+ row — not a model.

Same for `sp_plus_pred_home_pts` / `_away_pts` (aliases of
`model_pred_home_points` / `_away_points`).

**The SPREAD columns are NOT aliases.** `sp_plus_pred_spread` is computed
separately and genuinely differs from `projected_spread` (mean |diff| ~7
pts, 0/72 identical margins). Only the TOTAL is duplicated.

For a genuinely independent total use
`mc_probabilities.mc_expected_total` — 0/68 identical to
projected_total, mean |diff| 1.13.

**Why:** it had already leaked into two places as a second opinion.
(1) `ncaaf_sharp_fade_rules.rule_models_oppose_sharp` built a 2-model
totals consensus from projected_total + sp_plus_pred_total; identical
values can never disagree, so the `len(filled) >= 2` corroboration guard
was one model counted twice, emitting a STRONG fade claiming "Both
matchup, sp_plus oppose sharp." (2) GameDetailV2's Model Consensus
rendered v3/v4/SP+ totals as three lenses showing one number.
Both fixed in `d1482672`.

**How to apply:** before treating two columns as independent lenses,
test whether they are actually the same number on live rows. Applies to
any "consensus" or "N models agree" logic in any sport — agreement
between aliases is not evidence. Related:
[[project_ncaaf_3wk_calibration_920]], [[feedback_sample_size_with_pct]].
