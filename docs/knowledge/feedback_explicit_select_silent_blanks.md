---
name: feedback_explicit_select_silent_blanks
description: 🚨 A column missing from a PostgREST .select() does NOT error — it returns undefined and the UI renders blank. Audit client reads vs select list for EVERY sport whenever a select is narrowed.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-22T18:50:15.702Z
---

**A column absent from a PostgREST `.select()` does not error.** The
query returns 200 with the other columns, the client reads `undefined`,
and the card renders blank — silently, indefinitely.

**Why:** narrowing `SELECT *` to an explicit list is a normal egress
optimization, but it silently converts "every field works" into "only
enumerated fields work". Nothing fails loudly, so the regression is
found by a user noticing a blank panel weeks later.

**How to apply:** whenever a `.select()` is narrowed — or when a user
reports blank/партial panels — diff what the client READS against what
the query ASKS FOR, across every sport at once:

    reads   = every ctx.<field> / mlbCtx.<field> in index.tsx + GameDetailV2.tsx
    listed  = column names parsed out of the .select() literal
    missing = (reads ∩ live schema) − listed

Then live-validate the rebuilt list (expect HTTP 200/206). Generate the
list from that diff rather than hand-maintaining it.

## The 2026-09-22 case

Andy: "why are pitcher projections blank in game detail... something has
happened in the last two weeks that have borderline fucked up MLB...
pre analysis continue to be all fucked up for some games."

One cause. On **09-13** `mlb_game_context` went from `SELECT *` to
`MLB_CTX_COLUMNS` (75 columns). The client reads **142**. The other
**67** were dark for nine days:

  * all ten `*_pitcher_projected_*` columns (the empty projection card)
  * `sweat_breakdown` — the evidence list behind pre-game analysis
  * `align_status`, `spread_delta`, `spread_lean`,
    `consensus_fade_note`, `oddscrowd_snapshot`
  * the whole team-form block — wRC+, xwOBA, OAA, bullpen ERA, records,
    streaks, L10, days rest, platoon, catcher framing, 1st-inning splits

Data was never missing (16/16 games had projections). **The query
stopped asking.**

That also explains "not uniform": each card rendered whichever fields
survived the list, so one game looked complete and the next looked
gutted.

Auditing the other sports in the same pass found **NFL missing 23** and
**NCAAF missing 17** (off/def per-game splits, `consensus_fade_*`,
`matched_patterns`, `spread_anchor_weight`). Both had been returning 200
the whole time. NBA was clean.

Fixed e6f99ed8 + d866ba1a. Final: MLB 142 / NFL 88 / NCAAF 84 / NBA 37,
all HTTP 206.

## Sibling failure mode

The *inverse* is loud and already documented in those comments: a
PHANTOM name (column that does not exist) makes PostgREST reject the
WHOLE select with 42703, nulling the entire map — that bit MLB 09-13→09-19
(`supplementary_play`, `home_era`) and NFL 09-07 (MLB naming on NFL
columns). So:

  * phantom column  -> everything breaks, obvious
  * missing column  -> two thirds breaks, invisible  ← far more dangerous

Check for both. Related: [[feedback_backside_dictates_app_renders]],
[[feedback_validate_data_reaches_new_code]].
