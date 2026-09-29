---
name: project-lr-totals-investigation-908
description: "🎯 9/8 LR audit refinement: NCAAF total is shadow-only by design (model acc 52.4%), NFL total genuinely missing (no model file). MLB shadow signals are the immediate promotion opportunity."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T14:34:24.399Z
---

**Followup to cross-sport LR audit · 9/8 afternoon.** Initial audit
showed NFL total LR = 0% coverage and NCAAF total LR = 0%. Deeper
investigation reveals these are TWO different situations, not one bug.

## NCAAF total LR: intentional shadow-only mode

- Model file EXISTS: `mlb_pipeline/models/ncaaf_total_logreg.json`
- Code path EXISTS: `apply_ncaaf_total_lr_override` in defensive_gates.py:1082
- BUT line 1084 doc: "DEMOTE-ONLY mode. Test acc 52.4% (+3.5pp lift) —
  model too weak to promote picks"
- Behavior: always writes `_lr_total_shadow` field (100% coverage), never
  writes `_lr_p_over` to primary_play (0% coverage by design)
- Correct behavior for a weak model — signal is preserved as shadow so
  downstream can use it defensively without over-trusting it

**Not a bug.** The audit's "0% coverage on `_lr_p_over`" was measuring
the wrong field.

## NFL total LR: genuinely missing

- Model file MISSING: no `nfl_total_logreg.json` in models/
- Code line MISSING: defensive_gates.py:700-701 loads MLB + NCAAF, no NFL
  total
- Code path MISSING: no `apply_nfl_total_lr_override` function exists

**Real gap.** To close:
1. Backfill historical NFL games with computed totals + features
2. Train `nfl_total_logreg.json` (mirror the MLB training pipeline)
3. Add `_LR_MODEL_NFL_TOTAL = _load_lr_model('nfl_total_logreg.json')`
4. Add `apply_nfl_total_lr_override` function (mirror NCAAF pattern)
5. Wire into `recompute_primary_play.py` NFL path
6. Verify accuracy on holdout before promoting from shadow to primary

**Cost:** 4-8 hours of infrastructure + data work depending on data
availability. NOT tonight's scope.

## The immediate high-EV move

Shadow signals for both NCAAF (100%) and NFL (41% via existing paths)
ARE populating. The Sharp Card composer doesn't currently gate or
promote based on them. Wiring `_lr_total_shadow` / `_lr_ml_shadow`
into `generate_sharp_card.py` is a much shorter path to value:

- If pipeline pick + LR shadow AGREE → boost tier
- If LR shadow STRONG-disagrees with pipeline pick → cap tier at LEAN
  (or drop entirely)
- If pipeline surfaced ML but LR shadow says total STRONG → surface
  the alternative

Same pattern as [[project_lr_shadow_promotion_907]] — that memory
already captured this. Priority 1 to actually IMPLEMENT it.

## Sequencing

- **This week:** implement Sharp Card LR-shadow gate (uses existing
  data, no model training)
- **Next 2 weeks:** train NFL total model + wire it (real accuracy gain)
- **Cross-check quarterly:** retrain NCAAF total model with more games —
  once accuracy passes 55%, promote from shadow to primary

## Related
- [[project_lr_shadow_promotion_907]] — full spec for shadow promotion
- [[project_data_infrastructure_priorities_908]] — top priority stack
