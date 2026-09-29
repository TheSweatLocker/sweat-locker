---
name: feedback_postgrest_select_kills_whole_response
description: One non-existent column in a PostgREST select 400s the ENTIRE response; app renders it as an empty slate. Run verify_app_select_columns.py.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-20T00:42:04.119Z
---

PostgREST rejects the **whole** select if **one** named column does not
exist (42703 -> HTTP 400). There is no partial success. The app treats
`data == null` as "pipeline has not run yet", so a hard schema error
renders as a normal empty slate and nobody notices.

**Why:** this class has now bitten four times, and the MLB one shipped to
users in v1.0.1 and ran blank for six days (9/13 ef697869 -> 9/19), found
only when Andy screenshotted it. `MLB_CTX_COLUMNS` named
`supplementary_play`, `home_era`, `away_era` — none of which exist — so
every MLB game detail had `ctx = null` and MARKET / LINE MOVEMENT / STAT
PROJECTIONS / MONEY FLOW / Model Consensus were all blank at once.

It survived because nothing surfaced it: `result.error` was never
inspected, the catch was a bare comment, and `dbFetchCached`'s
last-known-good path cannot mask a schema error (cache is only written on
success, so there was never a cached copy and the stale banner stayed
silent too).

**How to apply:**
- Run `python mlb_pipeline/verify_app_select_columns.py` after touching
  ANY `.select()` in the app. Exits non-zero on a broken list. It found a
  second live bug (`external_source_track_record.n_graded`) on its first
  run.
- Two opposite failure modes, both real — do not conflate them:
  - column **missing from** the select -> that one field renders blank
    (NCAAF MC/SP+ tiles, [[feedback_validate_data_reaches_new_code]])
  - column **absent from the table** but named in the select -> the
    ENTIRE fetch 400s and every consumer blanks. Far worse.
- Whole screen blank across unrelated sections = suspect a 400, not a
  key-mismatch. Sections fed by a *different* fetch still rendering is
  the tell.
- Always log `result.error` distinctly from an empty result set.
- Verify against the live schema, never against a comment. The MLB
  regression had a comment asserting `supplementary_play` "only exists on
  mlb_game_context" — it exists on no table at all.

Related: [[feedback_backside_dictates_app_renders]],
[[feedback_publishable_view_drift]], [[feedback_sample_size_with_pct]]
