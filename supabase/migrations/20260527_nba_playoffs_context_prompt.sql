-- NBA Jerry-read prompt update: playoff context block (2026-05-27).
--
-- The struct now ships a `context.playoffs` boolean (date-based: May/June/
-- July or April 13+) and a `context.series_state` placeholder. Generated
-- by generate_nba_game_reads.build_struct.
--
-- This migration updates the NBA game_read_rules prompt template so Jerry
-- knows what to do with those fields. The prior rule block referenced
-- `isPlayoffMode true` which was never set in the struct (silent dead
-- rule). Replaced with an explicit context-based block:
--   - playoffs=true + series_state=null  →  NEVER cite series record /
--     game number / elimination scenario. Frame records as regular-season
--     baseline (no present-tense "this year").
--   - playoffs=true + series_state populated → lead The Setup with series
--     context. (Activates once we wire a games-API feed — queued v1.1.)
--   - playoffs=false → normal regular-season framing.
--
-- Already applied to live Supabase via PATCH. Migration is repo source-of-
-- truth so a fresh DB reproduces the same prompt state.
--
-- Idempotent guard: WHERE template NOT LIKE prevents double-append.

UPDATE prompt_templates
SET template = template || E'\n\nPLAYOFF-CONTEXT BLOCK (struct field ''context.playoffs'', added 2026-05-27):\n'
    || E'- The struct''s ''context'' block tells you whether playoffs are active and whether series_state data is available.\n'
    || E'- When context.playoffs == true AND context.series_state is null: DO NOT cite any series record, game-in-series number, or elimination scenario. We don''t have that data wired up yet. Frame any record references (home_record, away_record, wins, losses) as ''regular-season baseline of X-Y'' — never as ''this year'' or ''so far this season'' in present tense. Skip series narrative entirely.\n'
    || E'- When context.playoffs == true AND context.series_state is populated: lead The Setup with series context (leader, game-in-series, elimination scenario). Home-court advantage is earned. Down-3-1 historical comeback rate is 7%.\n'
    || E'- When context.playoffs == false: records are current. Normal regular-season framing.'
WHERE name = 'game_read_rules'
  AND sport = 'NBA'
  AND template NOT LIKE '%PLAYOFF-CONTEXT BLOCK (struct field%';

NOTIFY pgrst, 'reload schema';
