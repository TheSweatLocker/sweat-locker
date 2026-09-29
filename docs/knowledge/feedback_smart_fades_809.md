---
name: smart-fades-809
description: "Contradicting Jerry's own sim is GOOD analyst behavior when explicit counter-signal (sharp fade / cohort / form drift) is cited. Don't cap intelligent fades."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-09T17:52:40.579Z
---

**Rule:** Jerry picking against her own `jerry_pred_total` or `jerry_pred_spread` is legitimate and often correct when she cites explicit counter-signals (cohort data, sharp-money fade, starter form drift, bullpen fatigue, historical spot data, umpire tendency).

**Why:** User feedback 8/9 — "I dont feel like contradicting the model is bad honestly, it means we tracking other circumstance/signals and we are smart enough to fade our model here, i think thats a good look especially if it is considering sharp money fading when the math makes sense."

**Context:** The sim (`jerry_pred_total`) is ONE lens in a multi-lens system that includes:
- Sharp-money fade rules (per [[project_sharp_money_fade_808]])
- Cohort engine (`signal_confluence_net`)
- Historical spot hit rates
- Starter form drift (L3 ERA vs xERA divergence)
- Bullpen fatigue / opener risk
- Panel model (independent projection)

When 2+ counter-lenses point opposite the sim, fading the sim is CORRECT.

**How to apply:**
- The `sim_pick_direction_mismatch` style validator rule is TOO STRICT — should only fire when NO counter-signal phrase appears in the prose alongside the contradiction
- Counter-signal phrases to look for: "cohort says", "historically hits", "sharp money", "similar spots", "form drift", "1st-inning ERA", "bullpen", "gassed pen", "opener"
- If Jerry cites sim number + picks opposite direction + explains WHY (any counter-signal phrase) → SHIP IT, that's good analyst work
- If Jerry cites sim number + picks opposite direction + NO explanation → cap (that's the "Marlins v4 drift" pattern where prose contradicts itself)

**Brand angle:** Explicit fades are actually STRONG brand content. "Our simulator sees 10 runs but cohort data says similar spots hit UNDER 74% — take UNDER" reads as sharp, evidence-based analysis. It's the DIFFERENTIATOR — most tout services can't publicly disagree with their own model. We can.

**Don't over-correct:** Prior work built the `sim_pick_direction_mismatch` rule based on the Marlins bug (which was really about miscited numbers, not intelligent fades). The rule as written caps legitimate contrarian plays. Needs refinement per above logic.

Related: [[project_sharp_money_fade_808]], [[feedback_verify_ml_direction]].
