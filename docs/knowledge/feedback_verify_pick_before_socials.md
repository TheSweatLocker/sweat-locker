---
name: verify-pick-before-socials
description: Never recommend a specific play for Andy to post on social media without a direct DB verification query. Subagent-composed pick lists can hallucinate plays that don't exist in mlb_pipeline_props / nfl_pipeline_props.
metadata:
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-15T13:28:03.410Z
---

**Rule**: Any specific play (player + prop_type + line + odds + tier + conviction) I surface to Andy for social-media posting MUST be verified against the source props table via a direct query BEFORE presenting.

**Why**: On 2026-09-14 morning, a subagent I spawned to research "PRIME MLB props today" returned "Top 15 by conviction" including as #1: `Angel Martinez hits OVER 0.5 @ -150 (92) — CHW@CLE`. I passed that to Andy as a top pick candidate. He posted it (in the 6-play socials curation). Investigation the next morning found:
- Angel Martinez has NEVER had a `hits_over` prop generated in mlb_pipeline_props on ANY date
- Zero `hits_over` PRIME rows for 9/14 had conv ≥ 90
- The entire (player, prop_type, line, odds, conviction, tier) tuple was fabricated

The subagent output had a plausible shape (real player, real game context, plausible odds) that made hallucination invisible on read.

**How to apply**:
1. When a subagent returns a specific-play list, treat every row as UNVERIFIED until confirmed via a direct DB read of the source props table with the exact `(player_name, prop_type, prop_line, direction, game_date)` composite key.
2. If verifying 15 rows is impractical, verify the TOP 5-7 (the ones most likely to reach Andy's post) at minimum.
3. If the DB doesn't have the exact row shown, DROP it from the recommendation — never mention it to Andy without the disclaimer that it's unverified.
4. In socials-facing recommendations, prefer plays where I can cite `mlb_pipeline_props.id` or an equivalent verified anchor.

**Related**: [[feedback_validate_data_reaches_new_code]] (audit every call site on new field), [[feedback_backside_dictates_app_renders]] (server decides; don't fabricate), [[feedback_verify_player_team_first]] (verify player team/attribution first).

**Blast radius of a missed check**: A hallucinated play on Andy's public socials undermines the "we show you the receipts" brand promise. This is the exact opposite of what the app is built to do.
