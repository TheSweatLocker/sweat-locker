---
name: tomorrow-seed-collision
description: "Resolved 2026-06-09 — Tomorrow seed pattern is architecturally correct; bug class was date-less consumer queries. All app queries now date-scoped (line 7127 was the last leak)."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

The 2pm ET cron's "Build tomorrow preview slate" step (`.github/workflows/mlb_pipeline.yml:576-589`) runs `game_context.py --date $TOMORROW`, which intentionally seeds `mlb_game_context` rows for the next ET date. The Tomorrow tab in the app reads these. Series matchups recur on consecutive days, so the same `(home_team, away_team)` pair legitimately ends up with 2 rows for ~16 hours each afternoon. **This is by design** and is needed for the Tomorrow tab to populate with probable pitchers + opening lines + projections.

**Original bug class (now resolved):** Date-less consumer queries returned non-deterministic rows, sometimes picking up tomorrow's seed when the user was viewing today's data.

**Incidents:**
- 5/29: Numbers panel silently disappeared when `fetchMLBContext` got 406 PGRST116 on date-less `.single()`.
- 5/30: `fetchMLBContext` patched to date-scope all three lookup paths (commit `530b83a`).
- 6/9: A second date-less leak surfaced at `app/index.tsx:7127` (prop-context fetcher for K/HR/Hits prop narration). LAD card showed "Ohtani as pitcher" because the 2026-06-10 ghost row leaked through. Patched same turn (commit `4b43385`). Verified via grep that only one file references `mlb_game_context` (app/index.tsx) and all five lookup paths are now date-scoped.

**How to apply:**
- EVERY `mlb_game_context` query MUST date-scope. Non-negotiable invariant.
- Use `.eq('game_date', etDate)` with ET date derived from `game.commence_time` (or `prop.commence_time`, fallback to "today ET").
- Use `.maybeSingle()` not `.single()` so a duplicate-row case never throws — defense in depth.
- Do NOT introduce a separate `mlb_game_context_tomorrow` table; the seed pattern is correct and adding a second table would create migration risk for zero benefit given current consumer state. Reconsider only if N>3 date-less queries ever survive in the codebase.
- One-shot cleanup for any historical pollution: `DELETE FROM mlb_game_context WHERE game_date = '<polluting date>';` — won't affect today's rows, and the seed will respawn on the next 2pm cron.

**Symptom check when user reports "Numbers panel missing" or "wrong pitcher displayed":**
- Query `mlb_game_context?away_team=eq.<team>&game_date=in.(today,tomorrow)`.
- If 2 rows return for the same matchup across consecutive dates, the cron seed is doing its job. The bug is a date-less consumer — grep `from\('mlb_game_context'\)` in app and confirm every call has `.eq('game_date', ...)`.

Related: [[feedback_backside_dictates_app_renders]], [[project_pending_app_changes]]
