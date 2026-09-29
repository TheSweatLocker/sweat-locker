---
name: team_situational_records loses sports to every full CREATE
description: 20260905a and 20260906c each recreated the matview with only MLB/NCAAF/NFL, silently dropping NHL/NBA/NCAAB for three weeks. NBA/NCAAB still blocked by a season type clash. Found 2026-09-28.
metadata:
  type: project
---

`team_situational_records_full` is rebuilt by a **full `CREATE MATERIALIZED VIEW`** in every migration that touches it, and a full CREATE replaces the whole definition. `20260901e` added NHL, NBA and NCAAB blocks; `20260905a` and `20260906c` then each recreated it carrying only MLB, NCAAF and NFL, without naming the other three anywhere. Result: NHL had **0 rows** and the Situational Records card was blank on every NHL game — unnoticed for three weeks because none of the dropped sports was in season.

**Before changing this matview, count the sports in the new definition.** The `COMMENT ON MATERIALIZED VIEW` now says so.

**Two traps when restoring:**

1. **Do not rewrite the aggregation.** The real definition sequences each market separately (`seq_spread` / `seq_ml` / `seq_total`) so L5/L10 count the last 5/10 games in which *that market* resolved — the entire point of `20260905a`. A hand-rewrite using one shared `ROW_NUMBER()` over all games compiles fine and silently regresses the three working sports. Build the file by copying the existing blocks and aggregation byte-for-byte and inserting only what's missing.

2. **`season` type differs by sport.** `mlb/ncaaf/nfl_game_results.season` is INTEGER; `nba/ncaab_game_results.season` is TEXT (`'2024-25'`); `nhl_game_results` has **no season column** (derive it: month >= 9 → that year). A cast compiles but breaks lookups — `nhl_game_context.season` is `2026` (int) and matches, but `nba_game_context.season` is `'2026-27'` (text), so an int column never matches what the card queries for NBA and a text column breaks the four int sports. **NBA and NCAAB are still absent** pending that decision (NBA opens 2026-10-21, NCAAB 2026-11-03).

NHL also **excludes preseason** (`SUBSTRING(game_id FROM 5 FOR 2) IN ('02','03')`) — all 65 scored 2026 NHL games were exhibitions with only 4 carrying a `spread_result`. Same judgement as [[project_situational_grey_is_sample_floor_928]] and the preseason SOS/SOR removal.

Related: [[feedback_publishable_view_drift]], [[feedback_fix_at_root_three_parts]].
