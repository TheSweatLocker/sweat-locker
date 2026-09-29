---
name: Prop Jerry is now pipeline-driven, not EV-scanner
description: Props are generated server-side from proprietary matchup signals. No more EV-based grading.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
**Architectural decision** made 2026-04-22: Prop Jerry pivoted from an EV scanner (grade A/B/C/D based on market mispricing) to **pipeline-driven prop recommendations** using the proprietary signal we already compute (platoon-adjusted wRC+, xERA, L3 form drift, catcher framing, OAA, park, weather, umpire tendencies).

**Why:** EV scanners are commoditized — every public tool does them. The real moat is our deep pipeline. Generating top 15 conviction-ranked props per slate using proprietary matchup signal is both differentiation AND retention (daily habit: "what did the model see today").

**Current state:**
- `mlb_pipeline/generate_props.py` scores K props + Hits props from mlb_game_context fields
- Writes to `mlb_pipeline_props` table (tier: PRIME 80+, STRONG 65-79, LEAN 50-64)
- Runs after HR Watch in afternoon cron (needs confirmed lineups)
- Per-game cap of 3 hits props + lineup position bonus (leadoff +6, 3-5 hole +3, 8-9 -4)

**How to apply:**
- Future prop work is additive to this pattern — NBA follows same architecture with different signals (opp DefRtg, pace, injury-driven usage, L5 averages), UFC already partially there, NFL post-launch
- Jerry LLM writeups should NARRATE labeled signals from the DB row, not invent stats
- If user brings up "the EV scanner" or asks about A/B/C/D grades, those concepts are dead — redirect to conviction tiers
- DO NOT propose resurrecting EV-based grading unless user explicitly asks
