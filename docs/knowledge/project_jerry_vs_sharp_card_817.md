---
name: project-jerry-vs-sharp-card-817
description: 2026-08-17 architecture — Jerry picks EVERY game via ensemble; Sharp Card filters to ~4 sides/totals + 3-5 props/day
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-17T14:46:40.012Z
---

Every MLB game needs a Jerry opinion from the ensemble/playbook. Ensemble
must NOT return PASS-then-fall-back-to-legacy, because legacy compute
backtests at -10.8% ROI over 60d (vs ensemble +10.7%, 21pp swing).

**Layered architecture:**
1. **Ensemble scorer** — always returns a top pick per market for every
   game. Tier honestly (LEAN when weak, STRONG/PRIME when strong). No
   "below LEAN floor → PASS" gate.
2. **Jerry card (per-game reads)** — narrator layer on ensemble output.
   Every game gets a read + a primary_play. `_engine='ensemble_v2'` is
   the normal path; legacy fallback only fires when ensemble literally
   has zero opinions or health-suppresses.
3. **Sharp Card** — downstream filter on Jerry's output. **No hard cap
   on count** (8/17 clarification). PRIME/STRONG picks all publish; some
   days = 8 plays × 1u, other days = 3 plays with one at 2u. Confidence
   lives in **unit sizing** (2u when Sharp loves it), not in trimming
   the count.
4. **The Ledger** — chalk parlay builder (queued as Steam Room 4th tab).

**Why:** User called out on 8/17 that 6 of 10 games were falling back to
legacy compute despite legacy losing money. Jerry needs to have a take on
every game (product expectation); Sharp Card is where discipline lives.

**How to apply:** Any change to ensemble PASS behavior must preserve
"every game has a pick." Sharp Card generator does the trimming, not
the ensemble.

Related: [[project-ledger-teasers-817]], [[project-adaptive-model-ensemble-802]],
[[feedback-let-engine-speak]], [[feedback-confidence-in-first-pass]]
