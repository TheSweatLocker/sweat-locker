---
name: project-card-composition-audit-803
description: "14d app sweat card audit — totals 83%, props 46% (real drag); social should lead totals"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-03T23:58:20.355Z
---

**14-day app Sweat Card top_8 audit (2026-07-20 → 2026-08-02, n=102 graded picks):**

**Overall**: 57-45 (55.9%), +3.5pp edge over -110 break-even. Positive-EV process confirmed.

**By type — huge composition delta**:
- Over/Under totals: **83% W (n=12)** 🎯
- DotD (dawg picks): **73% W (n=11)**
- POTD: 67% W (n=3)
- ML sides: **62% W (n=16)**
- Props: **46% W (n=57)** ← drag
- YRFI: 33% W (n=3)

**By rank**:
- Top 4 picks: ~65% avg
- Ranks 5-8: ~45% avg
- Rank 6 specifically hits 33% — under review

**By tier — surprise**:
- PRIME: 51% W (n=68) ← barely break-even
- STRONG: 66% W (n=32) ← better than PRIME

**Actionable findings**:

1. **Prop pipeline needs calibration review.** 46% over 57 picks isn't variance — real calibration issue. Either tier thresholds too generous, projections off, or market efficient in this segment. Big investigation.

2. **Totals are underweighted in card composition.** 83% hit rate should be surfaced first. When totals available, they should be rank 1, not buried at rank 3.

3. **PRIME tier over-labeled.** STRONG (66%) beats PRIME (51%). PRIME threshold too loose OR PRIME plays (mostly high-conv props) systematically overvalued.

4. **Sorting by conviction actively hurts.** Top-5-by-conviction = 50%. Raw top_8 = 55.9%. Conviction sorting stacks the deck with PRIME props (46%) and loses the totals edge (83%).

**For social card curation going forward**:
- Lead with totals when available (highest hit rate)
- Cap prop count per card (limit the drag)
- Trust STRONG-tier plays as much as PRIME (or more)
- Don't sort purely by conviction — sort by (type_hit_rate × conviction)

**Next steps (product-side, not just curation)**:
- Prop tier calibration audit — WHY is PRIME 51% and STRONG 66%?
- Investigate whether prop conviction should be recalibrated (refit_conviction dampening)
- Consider promoting/demoting tiers based on this data

**Sample-size caveat**: n=12 for totals is small. n=57 for props is meaningful. n=68 for PRIME is meaningful. Findings on props + PRIME tier are actionable; totals lead is directionally strong but needs more n before radical composition shift.

Related: [[project_prop_tier_ux_confusion_720]], [[feedback_batter_hits_juice_trap_803]], [[project_composite_debias_finding_712]].
