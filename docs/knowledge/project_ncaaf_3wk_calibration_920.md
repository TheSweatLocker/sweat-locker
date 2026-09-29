---
name: project_ncaaf_3wk_calibration_920
description: "NCAAF 3-week calibration findings (2026-08-28..09-20, 217 graded games) - ensemble_v2 overrides its own projected_spread and loses; totals bleed; anchor cap validated"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T01:01:14.752Z
---

Assessment run 2026-09-20 over 217 NCAAF games with context + final
score (12 slate dates). Graded from RAW SCORES, not the stored
`*_result` columns (those carry a boolean-type bug on 4 rows and a
case bug on 4 more).

## 🚨 Sign footgun (fix first)
`projected_spread` and `close_spread` are stored in OPPOSITE conventions
**in the same table**. positive `projected_spread` = home favored;
negative `close_spread` = home favored. Verified on 122 rows: treating
projected as a raw home margin gives MAE 15.71, the other orientation
25.81. Anything comparing the two without flipping is silently wrong.
Extends [[project_close_spread_sign_bug_914]].

## Headline: the engine overrides its own model and loses
On the 66 games carrying BOTH a published `rl` pick and a
`projected_spread`, the published pick disagreed with the model's own
edge direction on **41 of 66 (62%)**:

    published pick            31-35  n=66  47.0%   -6.82u
    model edge, same games    36-30  n=66  54.5%   +2.72u
    where they DISAGREE:
      published               18-23  n=41  43.9%   -6.64u
      model side would be     23-18  n=41  56.1%   +2.91u

All 41 disagreements came from `ensemble_v2`. Suggestive, NOT conclusive
at n=66 (~5 games of difference) — run as a shadow before switching.

Caveat that must travel with this: `rl` overall was 87-65 (57.2%), so the
86 games WITHOUT a projected_spread went 56-30 (65.1%) while the 66 with
one went 47.0%. Projected_spread presence may mark harder games. Do not
quote the override finding without this.

## Model edge is real at MODERATE disagreement only
projected_spread edge vs market, following the model side (n=121):
    0-3   22-20  52.4%    3-7   25-14  64.1%  <<<
    7-14  10-5   66.7%    14+   12-13  48.0%  (bleed)
    ALL   69-52  57.0%  +10.72u
14+ disagreement is noise/data error, not edge. sp_plus_pred_spread is
similar but flatter (ALL 54.5%).

## Published record, 3 weeks
    rl     87-65  n=152  57.2%  +14.08u
    ml     20-13  n=33   60.6%   +5.18u
    total  12-19  n=31   38.7%   -8.09u   <- the bleed
Tiers do NOT separate: STRONG 58.2% (n=55) == LEAN 58.2% (n=79),
COVERAGE 50.9% (n=57), PASS 55.6% (n=18). PRIME n=7, ignore.

## Totals — earlier "use SP+" claim was WRONG (corrected 2026-09-20)
`sp_plus_pred_total` is a BYTE-IDENTICAL copy of `projected_total`
(72/72, mean |diff| 0.00) — one SP+ computation assigned to both columns
in ncaaf_game_context. The apparent 48.8% vs 55.6% gap was a time
artifact: sp_plus_pred_total only exists from 09-17, so it scored week 3
alone while projected_total carried all three weeks. On the SAME games
they score identically (25-20). There was never anything to switch to.
Fixed in `d1482672` — see [[project_ncaaf_duplicate_total_lens_920]].

## Anchor cap VALIDATED (confound-checked)
Anchor only fires from 09-12, so restricted to dates where both appear:
    anchor 0.75   19-27  n=46  41.3%  -9.73u
    anchor NULL   28-18  n=46  60.9%  +7.45u
Same slates, matched n. Real effect, not a time artifact. Keep the cap.

## Signal confluence net works above 0
net 0 = 28.6% (n=14) · net +1..+2 = 56.0% (n=150) · net >=+3 = 58.1%
(n=43). A net of exactly 0 should be a PASS, not a pick.

## Coverage gap
projected_spread present on only 122/217 (56%), improving by week:
43% (09-05) -> 57% (09-12) -> 74% (09-19). `sweat_tier_current` is NULL
on all 217 rows (unpopulated column, NOT survivorship).
