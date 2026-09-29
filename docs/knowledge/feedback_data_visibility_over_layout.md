---
name: data-visibility-over-layout
description: "Focus on correct/accurate data easily visible on game cards — scrolling is fine, sub-tabs only where genuinely needed. Don't over-engineer layout hierarchy."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-01T15:36:35.635Z
---

Stop optimizing for tab structure and information hierarchy when the real ask is data correctness + visibility. The current single-scroll pattern in GameDetailV2 is fine — scrolling is not a problem to solve. Add sub-tabs ONLY where a section legitimately needs them (e.g. Situational Results: switch between Spread / Total / Moneyline × Overall / L10 / H-A / Fav-Dog).

**Why:** User corrected me 2026-09-01 after I proposed a full tab-bar redesign in plot v1/v2. Quote: "You are focusing too much on the tabs themselves. I just want the correct data in the game card that is real accurate data and easily visible even if that means scrolling." Pre-launch pressure is data integrity, not layout aesthetics.

**How to apply:**
- When redesigning any card or screen, ask first: "is the data correct, complete, on cadence?" — if no, fix that before iterating design
- Preserve existing sections in their current order; don't reinvent hierarchy
- Sub-tabs are legitimate only when a section has clearly separable modes the user will want to switch between (Situational: yes; every card: no)
- Long scroll is Sweat Locker native — the "locker room binder" feel; embrace it, don't hide it behind tabs
- Restyle > redesign — the Sweat Locker distinctive treatment (tier spine, small caps, ribbons) applies to existing structure, doesn't replace it
- Related: [[project_game_cards_two_layer_901]], [[feedback_ui_and_pipeline_in_parallel]]
