---
name: feedback-let-engine-speak
description: "Don't cherry-pick cohort direction or pattern-match analysis on top of pipeline output — let the engine count both sides and report what it says"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

When pulling cohort plays for the user, do not surface a directional pick (OVER/UNDER, ML side, RL side) without first counting STRONG_EDGE+ confirms on BOTH directions of the same game. Cherry-picking the direction that fits a narrative is the exact failure mode the engine was built to prevent.

**Why:** Repeated occurrences in 2026-06-09 social-post ladder thread — surfaced WAS @ SF OVER without verifying it had cohort backing (POTD was actually on the Giants ML), then surfaced PHI @ TOR UNDER while the same game had 18 STRONG_EDGE OVER cohorts vs 8 UNDER. Same pattern-matching mistake twice in one turn. User: "Stop being sloppy."

**How to apply:**
- For ANY cohort recommendation, count STRONG_EDGE+ matches on the OPPOSITE direction of that same game before surfacing the play
- If the opposite side has more/stronger cohort confirms, the play is the opposite direction (or the game is a disagreement and shouldn't be recommended)
- Cross-check model consensus (v3/v4/Jerry/conf) with the directional cohort majority — they should agree
- Trust the engine's net count over my own narrative ("two shaky xERAs = OVER", "two aces = UNDER")
- Related: [[user_2026_roster_corrections]] — same family of error (trusting recall/narrative over pipeline data)
- Related: [[feedback_verify_pitcher_attribution]], [[feedback_verify_batter_team_attribution]] — verification discipline at the data layer
