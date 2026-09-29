---
name: feedback_verify_player_team_first
description: ALWAYS verify player team via system data (mlb_game_context away_pitcher/home_pitcher) BEFORE writing any pick case. Stop relying on stale player-team recall. Multiple repeated errors during 6/15-6/16 (Cease, Pete Alonso, others).
metadata:
  type: feedback
---

Hard rule before writing any pick case involving a player:

1. Query `mlb_game_context` for the date with `or:(away_pitcher.eq.{Name},home_pitcher.eq.{Name})` to confirm which team they pitch for AND which opposing team they face.
2. For batters, verify via player team field in props table.
3. NEVER write a writeup citing "facing X offense" or "his team" without confirming via system data.
4. Do not rely on what I know from training data — rosters change. 2026 roster facts I had wrong: Cease (was SD, now Toronto), Alonso (was NYM, now BAL), Ranger Suarez (was PHI, now BOS).

Failure pattern observed:
- 6/16 wrote "Cease facing K-prone Toronto" twice (he IS on Toronto, faces Boston)
- 6/15 wrote "Brewers ML" with wrong starter context
- Earlier weeks: Pete Alonso attribution errors

Why this is non-negotiable: when I get team attribution wrong in a pick writeup, user can't trust the rest of the analysis. The matchup case falls apart — wrong opposing offense profile, wrong park, wrong hitting profile. Errors cascade.

How to apply:
- Two-line verification at start of any deep workup:
    "Pitcher X — pitches for [TEAM] (away/home), faces [OPP TEAM]"
- Pull opposing-side offense profile (wRC+, K%, OPS vs opp hand, L5/L10 R/G)
- Then write the case
- If verification fails (player not found in tonight's slate), state that — don't guess

When the user says "Do Better" after team errors, this is the rule that needed to be in place.

Related: [[user_2026_roster_corrections]] [[feedback_verify_pitcher_attribution]]
