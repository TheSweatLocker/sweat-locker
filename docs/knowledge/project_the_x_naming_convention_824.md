---
name: project-the-x-naming-convention-824
description: "'The X' personality tag pattern for anything user-facing — extends existing brand voice (The Sharp, The Ledger, The Locker) to external handicapper attribution. Zero source-name exposure."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-25T01:29:43.285Z
---

**Design decision 8/24 — user approved during TestFlight prep.**

The app already uses "The X" branding for product surfaces (The Sharp, The Ledger, The Sweat Locker). Extending this to external handicapper attribution:

- Solves ToS problem — no source names displayed
- Reinforces brand cohesion — everything user-facing uses "The X" pattern
- Enables trust-building — users can learn "The Fade has been hot" faster than "Source H2 has been hot"
- Character-driven, memorable

## Personality tag candidates (5 sources currently)

Style-based assignment. Each source gets ONE stable tag reflecting their pattern. Recompute quarterly or when pattern shifts.

Available tags (avoid "The Sharp" — reserved for our own product):
- **The Chalk** — favorite-picker, ML-heavy
- **The Dog** — underdog-picker, +money bias
- **The Fade** — public-fader, contrarian
- **The Volume** — high pick count, breadth over depth
- **The Book** — line-based, market-follower
- **The Streak** — recent hot/cold rider
- **The Grind** — long-shot, high-var
- **The Lock** — high-confidence single plays
- **The Trap** — contrarian trap-setter
- **The Pulse** — market-momentum reader
- **The Angles** — situational specialist (weather, rest, splits)

## How to apply
When rendering ANY external handicapper attribution in the app:
- Never show the source name (BettingPros, PickDawgz, Pickswise, Odds Shark, Sports Chat Place)
- Never show initials (BP, PD, PW, OS, SCP) either — 8/21 abbreviation revision superseded
- Always show the personality tag: "The Chalk", "The Fade", etc.
- Tag assignment is a stable lookup — computed backend-side + rendered as-is

## Assignment strategy
- **v1 (launch):** manual seed based on source reputation + eyeballed pattern
- **v2 (post-30d data):** compute tag from source's own recent pick style (chalk%, dog%, fade rate, volume)
- **v3 (mature):** rotate tag if source materially changes style (unlikely — most sources are stylistically stable)

## Related
- [[feedback_tos_scrub_source_names]] — 8/21 abbrev rule (superseded by this)
- [[feedback_no_kenpom_attribution]] — same class of "hide the source, use the output"
- [[project_external_transparency_differentiator]] — external track record is a moat; personality tags help build it
