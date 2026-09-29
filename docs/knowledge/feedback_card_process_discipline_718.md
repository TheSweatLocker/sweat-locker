---
name: card-process-discipline-718
description: "Process discipline rules for card building after 7/17 inflated-conviction bad night. Cap card at 4 plays, cap conviction at 7/10 unless 4+ independent sources align, prefer sides when unanimity aligns."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Set 2026-07-18 after 7/17 card went 1-4 with 1 push.** Root cause: I inflated conviction labels (8/10, 9/10) on mid-strength plays. When they lost, followers saw "confidently wrong" instead of "honest edge attempt." Worse than just wrong.

## Rules

### 1. Cap public card at 4 plays maximum
**Why:** 6-play cards spread conviction too thin. If we're publishing 6+, at least 2 are marginal by definition. Marginal plays lose at ~50%, drag the card record down, and dilute the strong ones. 4 concentrated bets = higher signal density + easier to grade + easier to defend.

**How to apply:** when composing the public card, force-rank all considered plays. Take top 4. If #5 wants to sneak in, delete it — publish it as "supplementary" not "card-level."

### 2. Cap conviction score at 7/10 unless 4+ independent sources align
**Why:** My 8/10 and 9/10 labels on 7/17 were noise. Actual data support was 6/10-7/10 across the board. Anchoring on 7 as the ceiling prevents inflation from PRIME conviction scores or single-lens loudness.

**How to apply:** to earn 8+/10, a play needs 4 of these 5 aligned:
- Model consensus (3+ lenses agreeing on direction)
- Panel confirming direction (not just quiet, actively confirming)
- KEEP-bucket calibration (if prop)
- 2+ external handicappers agreeing (Greg, PickDawgz, DocSports, CBS)
- Line movement WITH our position (not against)

Fewer than 4 = cap at 7. That means most plays are 5-7. That's honest.

### 3. Prefer sides over totals when directional unanimity aligns
**Why:** 30d audit (July 18): Panel is 58.5% on sides (best of any lens), v3 is 57% on sides. Sides are the sharper cut when they align. Totals are 50-52% coinflip range for individual lenses.

**How to apply:** if a game has all-4 spread lenses agreeing on direction, that's a stronger signal than all-5 total lenses agreeing. Publish the side, not the total.

### 4. No SGPs on the "highest confidence" list
**Why:** SGPs multiply risk. Grading them as one line hides that you took 2-3 bets that had to all cash. Publish SGPs as personal-action extras, not card plays.

### 5. Only trust ELITE-tier gate outputs at full conviction
**Why:** 30d gate audit found ELITE 8-0 (100%) but STRONG 27% and LEAN 41%. Until the gate tier rewrite ships (queued), STRONG-gate picks max at 6/10 and LEAN-gate picks max at 5/10.

### 6. Juice check — flag when book has priced out the edge
**Why:** 7/18 CIN/COL YRFI at -190 illustrated this. The model signal was extreme (nrfi_score 0, both starters 6+ 1st-inn ERA) but book priced the juice at -190 = 65.5% breakeven. Historical YRFI hit rate at that model signal band is 57-67% — right at or below breakeven. **Book had already priced in the same info our model has.**

**How to apply:**
- KEEP-bucket props at −130 or better = fine to publish
- Any single-inning bet (NRFI/YRFI/1st-5) at −180 or juicier = juice has eaten the edge, skip or shop
- Standard sides at −180 or worse = size down or skip, especially for chalk teams
- Props at plus money = usually retain edge, safe zone

**When publishing a pick at −150 or worse juice, cite the breakeven % on the slide so followers understand the math.**

## The bigger frame

**Loud confidence is a liability.** Every 8/10 or 9/10 you post is a hostage to fortune — when it loses (and every card has losing nights), you look confidently wrong instead of just wrong. Restraint on the labels is a marketing win, not just an analytical one.

**Anchor to the track record page, not to individual pick labels.** The 90-day rolling PRIME rate speaks for the process. Individual pick conviction labels don't need to argue for it.

## Related

- [[project_30d_lens_audit_718]] — the audit that anchored these rules
- [[feedback_confidence_in_first_pass]] — earlier "honest confidence" feedback
- [[feedback_let_engine_speak]] — don't cherry-pick direction
- [[feedback_user_doubt_is_signal]] — user gut catches variance model misses
