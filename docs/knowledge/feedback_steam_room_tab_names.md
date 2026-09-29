---
name: feedback-steam-room-tab-names
description: "🎯 Steam Room sub-tab canonical names — 'The Split', 'The Ladder', 'The Sharp' (NOT 'Sharp Card' or 'Sharp Plays'), 'The Ledger'. User-facing copy MUST use current names."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T22:59:02.906Z
---

Steam Room sub-tabs — canonical user-facing names as of 2026-09-07:

- **📊 The Split** — sharp $ vs public bets across every source (internal state key `'lines'`)
- **🪜 The Ladder** — 1 pick/day compounding streak product
- **🎯 The Sharp** — disciplined sharp-money list (4 sides + 3–5 props/day, higher bar than Sweat Card). Actual tab label at [app/index.tsx:16165](app/index.tsx#L16165): `{id:'sharp',label:'🎯 The Sharp'}`. **NOT "Sharp Card" and NOT "Sharp Plays"** — user corrected 9/7 when I used "Sharp Card" in draft App Store copy.
- **🧾 The Ledger** — daily P/L + teaser + parlay builder

**Why:** Two separate credibility misses. First: "Line Movement" was renamed "The Split" and I kept using old name. Second (9/7): I used "Sharp Card" in App Store draft — that label is legacy code-comment / internal-doc naming only; the actual shipping label is "The Sharp". Any user-facing copy that says "Sharp Card" reads as internal jargon leaking out.

**How to apply:**
- User-facing copy (paywall previews, tour steps, marketing text, onboarding, FAQ, App Store): ALWAYS use the current display names above
- Code internals (state keys `'lines'`, `'sharp'`, `sharpTab*` variables, DB `surface='sharp'` in surface_records, old code comments referencing "Sharp Card"): OK to leave — cost of renaming exceeds value
- When surfacing Steam Room content in any new context, cross-check the tab label at [app/index.tsx:16162-16168](app/index.tsx#L16162-L16168) before shipping copy
- Search for stale names before commits (user-facing hits only, comments OK):
  - `grep -i "line movement" app/**.tsx`
  - `grep -i "sharp card\|sharp plays" app/**.tsx` — flag any hit that renders in the UI, not code comments

**Related:** [[feedback_sweat_card_vs_sharp_card]] covers the Sweat Card vs The Sharp distinction in full.
