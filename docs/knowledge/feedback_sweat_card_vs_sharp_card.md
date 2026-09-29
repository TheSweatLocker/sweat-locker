---
name: feedback-sweat-card-vs-sharp-card
description: Sweat Card (daily dashboard) vs The Sharp (Steam Room sub-tab). Different surfaces, different discipline. Never conflate. There is NO "Sharp Card" — that label doesn't ship.
metadata:
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T22:58:45.191Z
---

**Sweat Card** and **The Sharp** are two different surfaces. User corrected me 9/7 when I called it "Sharp Card" in draft App Store copy — the shipping label is **"The Sharp"**, not "Sharp Card" and not "Sharp Plays".

**Sweat Card** = the daily main dashboard. 8 curated picks + POTD + Dawg of the Day + Rest of Today's Card + yesterday recap. Broader inventory — sides / totals / props across all sports, tiered PRIME / STRONG / LEAN / READ.

**The Sharp** = a sub-tab inside the Steam Room. Disciplined sharp-money list — smaller cap, higher bar, sizing rules like heavy-fav halving, juice gates, MTD unit tracking as its own record surface. Actual UI label at [app/index.tsx:16165](app/index.tsx#L16165): `{id:'sharp',label:'🎯 The Sharp'}`. Also at [app/index.tsx:11696](app/index.tsx#L11696) onboarding: "THE SHARP · A disciplined sharp-money list. Smaller, higher bar than the daily Sweat Card."

**Internal-only aliases (do NOT put in user copy):** `sharp` state key, `sharpTab*` state variables, `surface='sharp'` in surface_records, and old code comments referencing "Sharp Card" (legacy naming, not user-facing). If updating those refs, keep them internally consistent but always render **"The Sharp"** in any user-visible string.

**Why:** Different mental models for different bettor personas. Sweat Card = "what's on the menu tonight," The Sharp = "the disciplined slice a sharp would actually play." Marketing + onboarding copy must keep them distinct — conflating them dilutes both products.

**How to apply:** When writing user-facing copy (onboarding, App Store description, help text, marketing, FAQ), always call them **Sweat Card** and **The Sharp**. Never write "Sharp Card" or "Sharp Plays" as user-visible text. When implementing features, size/discipline logic differs per surface — The Sharp gets stricter juice/cap rules ([[project_card_process_discipline_718]]) while Sweat Card is broader curation. Related surfaces in Steam Room: The Split (bets vs. handle), The Ladder (1 play/day compounding), The Ledger (parlays + teasers).
