---
name: project-ledger-teasers-817
description: "2026-08-17 idea — The Ledger builds teasers (move line into higher-prob zone + pair for even money), not just chalk parlays"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-17T14:45:04.618Z
---

Original Ledger concept was chalk parlay builder (combine 2-3 moneyline
favorites for even money). 8/17 user expansion: **teasers** are the more
interesting angle.

**Teaser mechanic:** Move the line into a higher-probability zone by
paying juice, then pair with a correlated leg to get back to even money.

* Total example: Over 8.5 → tease to Over 6.5. Juice climbs (–200ish),
  hit probability climbs (~75%+). Pair with another leg to restore even
  or better payout.
* Spread example: -9 favorite → tease to -5. Cover probability climbs
  meaningfully; pair with a correlated total to lock even money.

**Why interesting:**
* Uses existing signal pool — every game already has a directional take
  from ensemble/Jerry. Teaser just applies the correct line-move + pair.
* Casual-bettor friendly (translation not simplification —
  [[project-casual-bettor-ux-docket]]).
* Differentiator from any competitor's parlay builder — they don't
  factor sim-projected totals when picking tease amount.

**How to apply:** When we build The Ledger UI, don't limit to chalk-ML
parlays. Teaser paths should:
1. Read Jerry's directional take
2. Suggest tease amount that lifts hit% into a target band (e.g. 70-80%)
3. Pair with a correlated leg from the same slate to hit even-or-better
   payout math

Related: [[project-jerry-vs-sharp-card-817]], [[project-sweat-locker-ladder]]
