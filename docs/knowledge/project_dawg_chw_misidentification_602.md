---
name: project-dawg-chw-misidentification-602
description: 6/2 DAWG CHW pick was NOT a name-collision bug — CHW was legit dog at +132 (close_spread +1.5 RL), Jerry pointed at CHW winning by 2.74. Just a model loss.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Resolved 2026-06-03: NOT a bug.**

Verified against `mlb_game_context` for game_id `6c525054ef6bbee384a6400f05a7ef19` (CHW @ MIN, 6/2):
- `home_team` = "Minnesota Twins", `away_team` = "Chicago White Sox"
- `close_spread` = +1.5 (positive = home favored → MIN -1.5 RL, CHW +1.5 dog)
- `jerry_pred_spread` = -2.74 (negative = away wins → Jerry pointed at CHW winning by 2.74)
- ML on Odds API at pick time = CHW +132 / MIN ~-150 (consistent with -1.5 / +1.5 RL)

The Jerry-first selector correctly identified CHW as the ML dog, correctly saw Jerry pointing at the dog with magnitude 2.74 (well above the 1.0 gate), picked at PRIME 100, and lost. CHW went 4-6 in Minnesota. That's a model loss in the contrarian-dog cohort, not a misidentification.

**Why the original suspicion was wrong:**
- I conflated "CHW lost" with "CHW shouldn't have been picked as a dog"
- The user's "White Sox aren't dawgs today" comment was directional commentary (skepticism about the pick), not a factual claim that CHW was favored on the ML
- ML mapping uses tuple equality `(home_team, away_team)` against Odds API exact-name response — no collision path for "Chicago Cubs" vs "Chicago White Sox"
- The pre-game commence_time filter (added 5/28) prevents live-odds bleed-through

**No fix required.** The DAWG selector behaved as designed; the model just was wrong about Connor Prielipp / Sox bats matchup on the night.

**Real follow-up:** the new Jerry-first DAWG is still tiny-sample (a few picks in production). Continue logging results before any redesign. The lonely-Jerry vs co-signed cohort split flagged in `_jerry_qualifies_dog` docstring is the next audit cut to watch (~2 weeks of data needed).

**Related:** [[project_post_launch_roadmap_may_to_nfl]]
