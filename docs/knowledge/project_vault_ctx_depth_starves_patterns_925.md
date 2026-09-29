---
name: project_vault_ctx_depth_starves_patterns_925
description: Vault Match is fully built and flag-on; it shows nothing because game_context holds ~8% of a season
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-25T22:54:10.563Z
---

2026-09-25. Vault Match needs no client work — it is already shipped and
already backend-controlled:

- badge (🎯 BACK / ⚠️ FADE) + drawer with Wilson CI in `app/index.tsx`
- gated by `feature_flags` rows `ALL:vault_render` / `<SPORT>:vault_render`,
  which are **already TRUE** for ALL/MLB/NFL/NCAAF
- server writes `ctx.matched_patterns` via `attach_vault_matches.py`
- silent-hide contract: empty `matched_patterns` renders no chip

It renders nothing on all 351 upcoming games because **0 of 12 patterns in
`sport_pattern_registry` clear the gate** (n>=15, hit>=60%, Wilson>=0.52,
fresher than 36h). Best candidate `mlb_confluence_backs_prime` 64.5% on
n=31, Wilson 0.469 — fails on sample size alone.

**Root cause is not "no edge" — it is backtest depth.** `compute_sport_patterns.fetch_games`
backtests against `*_game_context`, and those tables are not archives:

| table | rows | span |
|---|---|---|
| mlb_game_context | 206 | 2026-03-23 .. 2026-09-26 |
| nfl_game_context | 316 | 2026-08-07 .. 2027-01-10 |
| ncaaf_game_context | 372 | 2026-08-29 .. 2026-10-10 |

A full MLB season is ~2,430 games; the table holds ~8%. MLB 30d/60d/90d
windows all return exactly 188 graded games, which is the tell. Any pattern
firing on a subset is capped at n=20-100, so Wilson can never clear 0.52
even at a 64.5% point estimate.

**Why:** the vault looked like a modelling failure and is actually a data
retention failure. Deciding "no patterns work" off a 200-game backtest
would be the wrong conclusion drawn from the right number.

**How to apply:** `team_recent_games` DOES have depth (8,772 MLB / 3,434 NFL
/ 30,654 NCAAF rows back to 2020). Patterns expressible over that table can
get real samples; patterns needing ctx-only fields (sharp money, dissent,
model tier) are permanently capped at ctx depth and should either be
rebuilt on an archived ctx or abandoned. Do not ship a "beta" Vault note
over an empty shelf — silent-hide is already the right behaviour. Related:
[[project_vault_match_901]], [[project_ufc_model_fades_lose_925]].
