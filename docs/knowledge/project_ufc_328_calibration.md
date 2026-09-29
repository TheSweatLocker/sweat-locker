---
name: ufc-328-first-real-model-vs-results-read-2026-05-09
description: "Card-level grade of UFC picks vs results; PRIME 3-0, STRONG 4-1, LEAN 1-4; method calls a coin flip. First data point that UFC pipeline is more legitimate than earlier \"TBD\" framing."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**UFC 328 (Chimaev vs Strickland, 2026-05-09) — 8-5 (62%) on winner picks. Method calls 6-7 (46%, coin flip).**

**Tier breakdown — clean discipline story:**
- **PRIME: 3-0** (100%) — Sean Brady (DEC), King Green (SUB), Baisangur Susurkaev (SUB)
- **STRONG: 4-1** (80%) — Joshua Van, Volkov, Gautier, Amosov all hit; Marco Tulio lost
- **LEAN: 1-4** (20%) — Volkov was the only LEAN hit; Chimaev/Gordon/Gomis/Carpenter all lost

**Main event:** Chimaev was LEAN 59% (low conviction). Strickland won by decision (upset). Tier discipline meant no credibility hit — model wasn't confident there.

**How to apply:**
1. **Drop LEAN tier from public-facing surfaces.** 20% hit rate on n=5 is below market implied probability. Either don't post LEAN picks or label them "low conviction / model lean, small sample." [[feedback_prop_jerry_odds]] suggests same hygiene for props.
2. **PRIME and STRONG UFC picks are publishable.** 3-0 PRIME + 4-1 STRONG mirrors the calibrated MLB tier pattern (PRIME outperforms STRONG outperforms LEAN by meaningful margins).
3. **Don't feature method calls as confident product output yet.** 46% method-match is roughly random — DEC bias in the model overstates decisions. Either omit method from the surface, or display as "model lean: likely decision/KO" with a caveat. Real fix needs more cards of audit data + a method-specific calibration pass.
4. **Upgrade UFC's product priority from "TBD" → "second-tier flagship contender."** [[project_launch_may_week1]] originally had UFC TBD; this data point says the pipeline is doing real work. Worth investing more time over summer.

**n=13 is one card.** Don't draw broad calibration conclusions yet. Need 4-6 cards of resolved data before tier hit-rates are statistically meaningful. Next major card data point will dial this in further.

**Specific fixes queued for the UFC build (not blocking launch):**
- Method-prediction recalibration — drop the DEC bias when finishing-rate signal is strong (KO/SUB artists)
- Public surface: hide LEAN tier picks OR add a "low confidence" badge
- Audit cohort: `ufc_picks_winner_prime` / `_strong` / `_lean` should mirror the MLB tier cohorts so calibration is auditable monthly
