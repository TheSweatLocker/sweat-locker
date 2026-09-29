---
name: nfl-has-two-thursday-locks-and-both-blocked-injury-news
description: The Thu 8am ET freeze exists in two independent places; a gate added to the pick builder is silently discarded by both. Found 2026-09-28 via the QB-injury gate.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-28T18:50:45.680Z
---

NFL picks and reads freeze Thursday 8am ET, and the freeze is implemented in **three** places that must all be satisfied before any Friday-through-Monday change reaches the app:

1. `nfl_game_context.py` — after `build_row`, restores the previously published `primary_play` wholesale from `existing_opens`. **Anything computed inside `build_row` is discarded here.**
2. `generate_nfl_game_reads.py` `run()` — a `jerry_cache` key (`game_read_{id}_nfl_week_{thu}`) skips the game entirely, before the prompt is built.
3. `generate_nfl_game_reads.py` `upsert_jerry_read_nfl()` — a second check that guards the write. **Unreachable for any already-generated game**, because #2 skips first.

**Why:** On 2026-09-28 I wired the new QB-injury gate into `build_row` and it fired correctly on all 5 affected games — then read-back showed **0 of 17 rows carried the stamp**. Lock #1 had restored the Williams-era pick. Fixing that revealed lock #2, which is why the first read-regen only touched the one game that had no prior read.

All three already *documented* the exception and left it manual:
- lock #1: *"no changes unless something significant happens"* (and claims it lets injuries refresh — it does not)
- locks #2/#3: `NFL_UNLOCK_WEEK=1` / `--force`, described as *"emergency injury regen or QB1 swap"*

A manual flag is no defence: Caleb Williams was ruled out Saturday the 27th and nobody was at a keyboard, so Monday's card shipped his projections.

**How to apply:** Any change that must reach a live NFL card mid-week has to be applied **after** lock #1's restore loop and **at** lock #2, not inside `build_row`. Always verify by reading the row back from the DB — the builder's own dry-run output shows the pre-lock value and will look correct while the written row is stale. Releases should be narrow and self-limiting (release only while the frozen artifact lacks the new fact), never a blanket unlock. See [[project_public_receipts_integrity_918]] — cap and disclose, but never rewrite side/label/market on a published play.
