---
name: game-cards-two-layer-901
description: "9/1 directive — every game card is two layers: (1) our models/picks, (2) raw stats + intricacies we base picks on. MLB is the parity reference; NCAAF list currently cheap."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-01T15:00:50.270Z
---

**User directive 2026-09-01:** every game card (list screen + detail screen) must expose TWO layers of information:

1. **Layer 1 — Our take:** the model/ensemble pick, tier, sharp $ split, confidence, "why" summary. This is what already exists on MLB cards.
2. **Layer 2 — Raw stats + intricacies:** the underlying data our picks are BASED on. Team stats (raw + rank), matchup context, situational splits, tendencies, ranks. Currently thin/missing across sports.

**Current state gap:**
- **NCAAF game cards on list screen look cheap:** only 1 badge (the pick, e.g. "Missouri ML"), only Hard Rock Bet lines, no sharp/public splits, no lens summary
- **MLB game cards look way better** — use MLB as the parity reference when porting to NCAAF/NFL/NCAAB
- Detail screens across sports miss Layer 2 almost entirely — no raw stat rows, no situational W-L filters, no H2H schedule, no stat leaders

**Why:** A pick without visible reasoning reads as a gimmick. Bettors trust picks they can validate — showing the raw stats and intricacies IS the validation. Also serves casual-bettor UX ([[project_casual_bettor_ux_docket]]) — translation, not simplification.

**How to apply:**
- Cross-sport parity: whatever MLB game cards show, NCAAF/NFL/NCAAB cards should show equivalents (with sport-appropriate fields — e.g. QB matchup for football instead of pitcher matchup for MLB)
- Multiple sportsbooks, not just Hard Rock, in the odds display (contractual color for HRB stays ([[feedback_brand_attribution_803]]) but competitors' lines are surfaced too)
- On the game detail screen, the Trends/Stats tabs are where Layer 2 lives — raw stat + rank rows, situational W-L, recent schedule, stat leaders
- Any pipeline work to enable this is table-stakes pre-launch, not a nice-to-have

**Design vibe:** Sweat Locker's own palette (gold mahi #E5B227 + cyan #3BAECF + cream #F4EEDC on deep steel-blue #22323E — see [app/theme.ts](app/theme.ts)). Do NOT copy competitor visual language ([[project_game_detail_action_network_vision_901]] reference is for data patterns only, not aesthetic).

**Related:**
- [[project_game_detail_action_network_vision_901]] — data pattern reference (raw + rank, situational tabs, H2H schedule)
- [[feedback_college_sports_no_props]] — NCAAF/NCAAB skip Props
- [[feedback_ui_and_pipeline_in_parallel]] — plot/design in parallel with backing pipeline
