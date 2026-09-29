---
name: full-slate-artifact-format
description: "When user asks for full per-game breakdown across all slate games, publish as HTML artifact rather than chat dump. Chat markdown fragments the read; artifact renders scannable."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Set 2026-07-19.** For "full breakdown with EVERYTHING easy to read" requests covering the full slate (all 16 MLB games or equivalent NFL/NBA), publish as a styled HTML artifact rather than dumping into chat.

## Why

- Chat markdown truncates or fragments when the data volume is high (16 games × 8 sections = >2500 lines)
- User validated the artifact format worked ("the artifact was helpful") after asking for full slate breakdown 7/19
- Chat dump requires user to scroll linearly; artifact has sticky-nav for game-jumping
- Tables render cleaner in dedicated page than in chat's rendered markdown

## How to apply

- Requests like "full breakdown for each game with EVERYTHING" or "full comprehensive look" → build HTML artifact
- Requests like "quick take on this game" or "which of these picks looks good" → stay in chat
- Structure the artifact with the RECOMMENDATION AT TOP (the answer they need first), then deep-dive per game
- Include: model lens (all 5), Panel, situational, pitcher projections, team offense, cohorts (all matched rules), resolver, best props, externals
- Design: dark ground + accent color + monospace numbers + sticky nav

## Fallback

If artifact link 404s (user not logged in claude.ai/code), offer to dump inline in chunks (4 games per message).

## Related

- [[project_pending_app_changes]] — pipeline data source
- [[feedback_backside_dictates_app_renders]] — server-side data truth
