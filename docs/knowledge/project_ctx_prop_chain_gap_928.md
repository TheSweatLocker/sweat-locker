---
name: a-sport-slot-renders-nothing-unless-its-ctx-map-is-wired-in-four-places
description: "GameDetailV2's ctx comes from a per-sport map in index.tsx. NHL and NBA had slots but no map, so both were dead. Found 2026-09-28."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-28T19:31:27.823Z
---

`GameDetailV2` does **not** fetch its own `ctx` — it receives it as a prop from a per-sport map built in `app/index.tsx`. Adding a sport slot without wiring the map ships a screen that renders nothing, silently.

**Four places must all know the sport:**
1. `fetchMLBGameContext()` in `index.tsx` (a misnomer — it loads every sport's context table) must fetch `<sport>_game_context` into a map keyed by `game_id` AND `away@home`.
2. The `ctx={...}` prop chain at the `<GameDetailV2>` call site. It was `MLB → NFL → NCAAF → null`.
3. The `sportMap` used for game-card tier badges — falling through to `mlbGameContext` means a badge can come from whatever MLB row shares a team name.
4. The `ctxMaps` record used for pick matching.

**Why:** On 2026-09-28 I wrote the NHL slot, and it would have shipped completely invisible — `ctx` was `null` for NHL because no map existed. NBA had the same gap since 09-21 (`nbaGameContextMap` existed but was never wired into the chain), so `NBASlot` was dead too. NCAAB still has no map as of this writing.

**How to apply — verify with data, not by reading code:**
- Test the exact `.select(...)` string against the live table first. One bad column name makes PostgREST **400 the whole select**, so the map stays empty and every card blanks. See [[feedback_explicit_select_silent_blanks]].
- Then confirm live Odds-API events actually resolve to ctx rows. Two traps found for NHL: `nhl_game_context.game_id` is the NHL's own id (`2026020005`), which matches an Odds-API event id **0 times** — only the name key resolves. And `"St Louis Blues"` (Odds API) vs `"St. Louis Blues"` (ctx) lost 3 of 33 games to one period, which the substring fallback could not recover. A punctuation-stripped alias key fixed it; accents did **not** need folding (both sources write `"Montréal Canadiens"`).

Related: [[feedback_validate_data_reaches_new_code]], [[feedback_backside_dictates_app_renders]].
