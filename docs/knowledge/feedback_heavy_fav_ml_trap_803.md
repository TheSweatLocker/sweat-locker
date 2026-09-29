---
name: feedback-heavy-fav-ml-trap-803
description: "Heavy-fav ML at -200+ is trap — market pricing efficient, our models overweight, variance asymmetric"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-03T20:11:44.536Z
---

**Rule**: Avoid heavy-favorite ML plays at -200 or juicier. If the game read likes the favorite, look for a correlated market (total, prop) at fair price instead.

**Why**:
- 60-day data (2026-06 to 2026-08): heavy favs hit **below their implied win rate** across every juice bucket:
  - -200 to -249: 60% hit, 69.1% implied → **-9.1pp / -13.2% ROI** (n=5)
  - -250 to -299: 67% hit, 73.3% implied → **-6.6pp / -9% ROI** (n=3)
- Real trap examples that validated this pattern:
  - **8/2 Brewers -232**: MC said 83% win. Market priced 30% dog chance. Brewers lost 0-3.
  - Companion to [[feedback_juice_fav_rl_trap_724]] (RL version: -200+ favs cover only 29%)
- Mechanics: market is efficient at heavy juice, our models often overweight recent form + matchup edge, variance is asymmetric (-$217 loss vs +$100 win requires 68.5% BE — small hit-rate miss = catastrophic ROI)

**How to apply**:
- When Jerry likes a favorite at -200+ ML:
  - Check if the same directional read has a **correlated market** at fair price
  - Total UNDER (if favorite is dominant SP) → cashes when fav wins by shutting down offense
  - Player prop on the fav's key player → cashes on the same script
  - Runline is even WORSE than ML per [[feedback_juice_fav_rl_trap_724]]
- Example (8/3 slate): Yankees ML -217 (thin +3.6pp edge) → swapped to Yankees UNDER 7.5 (+11.3pp edge on same directional bet). ~3× the EV, same conviction.
- Never publish heavy-fav ML as POTD — POTD should be a play with real EV, not a coin-flip winner at juice tax.

Related: [[feedback_juice_fav_rl_trap_724]], [[feedback_sharp_money_discipline_802]], [[project_composite_debias_finding_712]] (Jerry sometimes overweights momentum on favorites).
