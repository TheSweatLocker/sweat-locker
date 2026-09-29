---
name: verify-batter-team-attribution-before-listing-bat-fade-stacks
description: "When recommending hits-under stacks, ALWAYS verify which team each batter plays for by checking which pitcher's xERA appears in their \"why\" list. Mixed up Kim/Albies (ATL bats) with Benintendi (BOS bat) on 5/16 BOS/ATL card. User: \"you keep mixing teams and hitters up. This needs to stop!\""
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Rule:** Before recommending any hits-under prop as part of a "fade [Team] bats" stack, verify the batter's team by checking which pitcher's xERA is referenced in the prop's `why`/`signals` field.

- Prop's `why` says "Opp starter 1.99 xERA" → the batter faces the pitcher with 1.99 xERA → batter is on the OTHER team
- Map: BOS pitcher Tolle 1.99 xERA → if "Opp starter 1.99" appears in the why, that batter is on ATL
- Map: ATL pitcher Elder 2.90 xERA → if "Opp starter 2.90" appears in the why, that batter is on BOS

**Why this matters:** [[feedback_verify_pitcher_attribution]] documented the pitcher-side version of this attribution bug (Suarez/Luzardo 5/14, then the Jerry "Boston wRC+ Tolle will punish" bug 5/16). I keep making the *batter-side* version of the same bug — calling players the wrong team when constructing stack recommendations.

**5/16 specific instance:** On the BOS/ATL "Braves Stack" card, I called Kim/Albies/Benintendi all "BOS bats" to fade. Actually:
- Ha-Seong Kim (PRIME 99) — facing Tolle (BOS) → Kim is on **ATL**
- Ozzie Albies (PRIME 87) — facing Tolle (BOS) → Albies is on **ATL**
- Andrew Benintendi (PRIME 92) — facing Elder (ATL) → Benintendi is on **BOS**
- Edgar Quero (STRONG 84) — facing Elder (ATL) → Quero is on **BOS**

User caught it before any of these went in a public card. This is the SECOND attribution-error catch in the same day.

**How to apply:**
1. Before listing batters as a "fade [Team]" stack, **for each batter** check what xERA appears in their `Opp starter X.XX` why-line.
2. Map that xERA back to the pitcher → that pitcher's team is the OPPOSING team → the batter is on the other team.
3. If the card thesis is "back Team A" → fade Team B bats only. Mixing Team A bats into the fade contradicts the thesis.
4. **Distinct theses for the same game:**
   - "Back home ML" → fade away bats
   - "Back away pitcher Ks" → fade home bats
   - Don't conflate these into a single stack.

**Why I miss it:** Both Kim and Albies appear in our PRIME hits-under list AND in the same game as Benintendi/Quero, AND the matchup string ("Boston Red Sox @ Atlanta Braves") is identical for all four. The disambiguator is the opp-starter field in the prop's why. I have to read it every time.

**See also:**
- [[feedback_verify_pitcher_attribution]] — the pitcher-side analog
- [[user_2026_roster_corrections]] — 2026 trades amplify the risk (players I associate with one team are now on another)
- [[project_jerry_opp_lineup_attribution]] — the prompt-side fix for the same family bug
