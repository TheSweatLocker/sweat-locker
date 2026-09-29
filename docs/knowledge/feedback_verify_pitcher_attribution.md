---
name: verify-pitcher-vs-team-field-attribution-before-featuring-mastery-claims
description: "Field-attribution rule + verification step. Caught attributing Luzardo's 10.8 ERA vs Boston to Suarez (Boston's pitcher now). Wasted a card draft."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Rule: in `mlb_game_context`, the `_pitcher_vs_team_era` fields belong to whoever is named in the same prefix:**
- `away_pitcher_vs_team_era` → the AWAY pitcher's career ERA vs the HOME team's lineup. ALWAYS the away pitcher's number.
- `home_pitcher_vs_team_era` → the HOME pitcher's career ERA vs the AWAY team's lineup. ALWAYS the home pitcher's number.

**Why this matters:** [[user_2026_roster_corrections]] notes that I keep getting 2026 roster moves wrong (Alonso → Orioles, Suarez → Red Sox). When a pitcher has been traded, my brain still associates them with their old team, and I match their name to the "wrong half" of the row. The 5/14 PHI/BOS card draft attributed Luzardo's 10.8 ERA vs Boston to Suarez (because Suarez "used to be a Phillie") — completely fabricated mastery story. User caught it before public post.

**How to apply:** Before ever writing "X has Y ERA vs Z lineup" in any public-facing card or content:
1. Pull the specific row. Confirm which pitcher is `away_pitcher` and which is `home_pitcher` in 2026 (NOT from memory).
2. Map the ERA number to the matching prefix-pitcher.
3. If a "mastery" or "fade" angle hinges on that number, verify the IP sample size if possible — many of these are 3-5 IP samples that noise out at the extremes.
4. If the pitcher recently changed teams (Suarez, Alonso, Pete Alonso, etc.), assume my recall of which team they're on is stale and pull the data fresh.

**Specific anchor for next time:**
- `away_pitcher` + `away_pitcher_vs_team_era` always pair
- `home_pitcher` + `home_pitcher_vs_team_era` always pair
- Don't read them across positions even when the narrative feels right.
