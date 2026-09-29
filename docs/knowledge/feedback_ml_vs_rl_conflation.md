---
name: feedback-ml-vs-rl-conflation
description: "NEVER conflate ML-winner and RL-cover reads. They answer different questions and often disagree — a \"6/6 lens confluence\" for spread cover can be only 4/6 for ML."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-27T20:36:16.588Z
---

**Rule:** Every "side" chip / recommendation must be explicitly labeled as
either "WINS ML" (who wins outright) or "COVERS SPREAD" (who beats the
runline). These are DIFFERENT bets and lens agreement for one does not
imply agreement for the other.

**Why:** 2026-07-27 the public post recommended DET ML -120 as a "6/6 lens
confluence lock." The 6/6 was actually spread-cover math (DET covers +1.5).
For ML, only 4/6 lens had DET winning outright (2 had BAL winning narrowly).
Followers took DET ML thinking every model agreed. If DET loses by 1, they
lose ML but would have won the +1.5 runline.

Same day: CLE @ CIN morning writeup said "5/6 lens on CLE." Reality was
5/6 lens had CIN WINNING (positive margin) but by less than 1.5 — so CLE
covers RL +1.5. Anyone reading "5/6 on CLE" and taking CLE ML was betting
directly against the model consensus.

**How to apply:**
- The `_slate_analyze_v2.py` script computes ML and RL picks as separate
  fields (`_ml_picks`, `_rl_picks`, `_ml_lead`, `_rl_lead`). Use it.
- When writing recommendations, always cite BOTH:
    "5/6 lens on CIN ML" (who wins) vs "5/6 lens on CLE +1.5" (who covers).
- When the two DIVERGE (a positive margin lens for the home team where
  |margin| < |close_spread|): the home team wins the ML but the away team
  covers the runline. Both plays are "consistent with the models."
- Never post a game as a "6/6 lens lock" without specifying which market.
- Public posts get double-checked: if my recommendation is ML, verify that
  the ML-side count is what I think it is, not the RL-side count.

**Divergence rate:** ~50-60% of MLB games have ML lens count ≠ RL lens count
because margin predictions cluster narrowly around 0-2 runs where the +/-1.5
runline flips the direction. This is not rare — it's the majority of games.

**Related:**
- [[feedback_verify_ml_direction]] — the older version of this rule, less
  specific. This memory supersedes when the two conflict.
- [[project_juice_fav_rl_trap_724]] — juice fav ML plays don't map cleanly
  to RL plays; another instance of ML vs RL being separate bets.
