---
name: Inning bucket bets — premium positioning + product priority
description: Strategic decisions on how to surface inning bucket data/picks in the app
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
**Decision (2026-04-28):** Inning bucket data + bucket bet picks ship as **premium tier only** — they are the product moat. Free tier keeps existing surfaces (POTD, Daily Degen, HR Watch); premium unlocks bucket-level data and bucket edge picks.

**Phase priority (reversed from my initial proposal):**
- Phase 2 (actionable Bucket Edge picks with PRIME/STRONG/LEAN tiers in Jerry) > Phase 1 (raw bucket data on game detail card)
- Reasoning: users are lazy and won't dig into game detail to interpret data themselves. They want surfaced actionable edges. Raw data card can come later as enrichment.

**Variance framing for marketing:** Bucket bets are "less toxic than NRFI" because they have 3 innings of probability surface vs NRFI's binary 1-inning-1-run resolution. Single fluky plays don't wreck a 1-3 UNDER bet the way they wreck a NRFI. This bet-structure edge is part of the pitch alongside the data edge.

**Why:** Andy spent 4 hours building the inning bucket data layer (commit 94aff91) and immediately recognized the moat potential. Bucket bets passed the personal-use test on day 1 (parlay built, optimistic on outcome). If results validate over Week 1, this becomes the anchor feature for premium subscription pitch.

**How to apply:** When Andy revisits app surfacing for inning buckets, default to Phase 2 first (picks-with-tiers in Jerry section), gate behind premium, and frame the variance advantage in marketing copy. Don't build raw data exposure on game detail until picks tier validates.
