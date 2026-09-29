---
name: college-sports-no-props
description: NCAAF and NCAAB have NO Props section. Drop Props tab + all prop surfaces for college sports.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-01T15:00:31.051Z
---

College sports (NCAAF, NCAAB) do NOT surface player props. Every game-detail tab bar, list-screen filter, and card surface must omit the Props tab / prop chips for these sports. Only MLB, NFL, NBA, NHL, UFC get Props.

**Why:** Data coverage for college player props is thin and unreliable (books offer fewer markets, sample sizes small, prop tracking noisy). User directive 2026-09-01. Also aligns with the college scope decisions — we already deferred college NCAAB V4 for similar data-thinness reasons ([[project_ncaab_v4_deferred_814]]).

**How to apply:**
- When building any new game-detail layout, tab bar, or card, gate Props tab on `sport IN ('MLB', 'NFL', 'NBA', 'NHL', 'UFC')`
- When porting plots / mockups cross-sport, remove Props tab for NCAAF/NCAAB variants
- Never suggest a "college prop backfill" — the coverage isn't there
