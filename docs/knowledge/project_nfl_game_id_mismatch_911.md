---
name: project_nfl_game_id_mismatch_911
description: "NFL/NCAAF game_id split — ctx/jerry use Odds hash, results use schedule format. Silently zeroed grading since launch. Three call sites patched with tuple lookup; nfl_odds_pull seeder still emits legacy format going forward."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-15T15:05:10.747Z
---

# NFL/NCAAF game_id landmine — silent grading failure since launch

`nfl_game_results` and `ncaaf_game_results` seed their `game_id` from
a schedule-format string (`20260910_NE_SEA`, `ncaaf_20261212_Army_Navy`)
built inside [nfl_odds_pull.py:142](mlb_pipeline/nfl_odds_pull.py#L142)
and the NCAAF equivalent. `jerry_reads`, `nfl_game_context`, and
`nfl_game_picks` all carry the **Odds API event hash**
(`8c94552d022acec4a0458d70c19d3da9`). Every downstream `.get(game_id)`
or `?game_id=eq.<hash>` PATCH failed against the results tables —
PostgREST returned 204 (silent no-op on WHERE-matches-0) and the
grading scripts even incremented their success counters.

**Why:** Comment at line 133 says "We don't have week/season from Odds
API directly. Use start-time-derived game_id: YYYYMMDD_AWAY_HOME."
Reasonable in isolation but breaks every joiner. The Odds API event's
`id` field is the natural key.

## Patched call sites (2026-09-11)

1. [mlb_pipeline/resolve_nfl_results.py](mlb_pipeline/resolve_nfl_results.py) — `refresh_results` and `fetch_result_map` both switched to `(away_team, home_team, game_date ±1d)` tuple join. Commit 249d71cb.
2. [mlb_pipeline/grade_jerry_reads.py](mlb_pipeline/grade_jerry_reads.py) — NFL/NCAAF branch enriches reads via `<sport>_game_context` to recover teams, then joins by `(away, home, week_bucket=most_recent_Thu)`. Commit 7b0b10a5.
3. [mlb_pipeline/compute_surface_records.py](mlb_pipeline/compute_surface_records.py) — `_pick_generic_sides` switched to same tuple. Commit 7b0b10a5.
4. [mlb_pipeline/resolve_externals.py](mlb_pipeline/resolve_externals.py) — `fetch_result_map` NFL branch bridges through nfl_game_context to recover teams. Commit 2dbac4a5.

## Not patched — future work

- **Seeder itself.** [nfl_odds_pull.py:142](mlb_pipeline/nfl_odds_pull.py#L142) still generates the legacy id. Long-term fix: prefer `event.get('id')` when present, keeping legacy as fallback. Would need a one-shot backfill patching existing 2026 results rows so game_id joins work everywhere. Deferred because tuple lookups now cover the graders that actually matter.
- **Historical rows** (2020-2025) never had an Odds hash. If we ever swap the seeder, gate on `season >= 2026`.

**Why:** Fixing the seeder ripples through every downstream reader; risky mid-season. Tuple lookups are cheap and each patched site takes <25ms extra per run.

**How to apply:** When adding a new NFL/NCAAF grader or record-computer,
join `<sport>_game_context` (Odds hash) or `jerry_reads` (Odds hash)
against `<sport>_game_results` (schedule format) via `(away_team,
home_team, week_bucket)` tuple, never raw `game_id`. Same pattern as
the three sites above. Related: [[project_cross_sport_grading_audit_908]].

## Verified impact 2026-09-11

- NE@SEA 10-13 total=23 → Jerry "Under 44.5" = **Win**
- SF@LA 27-7 total=34 → Jerry "LA ML" = **Loss**
- surface_records nfl_sides: 1-1, -0.09u, 2 picks (all windows) — matches Andy's expectation for Wk1.
- NFL Games tab Receipts now populate; grader runs after resolver in nfl_pipeline.yml.
- NCAAF grader wired in ncaaf_pipeline.yml (0 new grades because NCAAF was already covered by a different result path — belt+suspenders coverage).

## Full audit 2026-09-15 (post-c9a06d3b)

Sampled 200 recent rows each side (game_date >= 2026-09-01):
- **100% of nfl_game_context** rows use MD5-hash-32 (`5ad8135d…`)
- **100% of nfl_game_results** rows use YYYYMMDD_AWAY_HOME (`20260915_DEN_KC`)
- **0 game_id matches** across the two tables — total schism
- **155 same-game/different-id pairs** confirmed by composite (date,home,away) join

Additional writer patched today (c9a06d3b): [nfl_results_backfill.py:171](mlb_pipeline/nfl_results_backfill.py) `upsert_batch` was creating DUPLICATE rows with nflverse ids instead of updating existing YYYYMMDD_AWAY_HOME rows. Now PATCHes existing rows via composite lookup, INSERTs only when no composite match exists. Root cause of MNF DEN@KC 9/15 scores staying NULL despite game being over.

## Deferred long-term unification

Standardizing on YYYYMMDD_AWAY_HOME for both tables would:
1. Require every `nfl_game_context` writer to compute the schedule-format id (currently derived from Odds API event hash)
2. Require a one-shot backfill re-stamping every existing context row (~200-400 rows × 32-char id rewrite + audit that no downstream consumer breaks)
3. Have to happen atomically across all sports simultaneously if we want cross-sport code parity

Not doing today. Composite-key defensive joins cover every hot path we've hit so far. If a NEW consumer trips this landmine again, follow the pattern: join by (game_date, home_team, away_team) with ±1-3 day window tolerance.
