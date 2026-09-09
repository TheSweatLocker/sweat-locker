-- 2026-09-09: NCAAF ctx blend label + games-played columns.
--
-- ncaaf_game_context.py 800361fb writes 4 new fields per row so the app
-- can render "blended · 2 games this season + 2025 season" caption below
-- the team stats block, and so downstream consumers know sample size.
-- pgrst_strip_retry silently drops them until this migration lands.
--
-- Same fields also duplicated inside home_team_stats_summary /
-- away_team_stats_summary JSONB (as `blend_label` + `games_current`) —
-- those are already supported because they're keys inside JSONB, no
-- schema change needed for those. This migration is just for the
-- top-level convenience columns.

ALTER TABLE ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_stats_blend_label text,
  ADD COLUMN IF NOT EXISTS away_stats_blend_label text,
  ADD COLUMN IF NOT EXISTS home_games_played int,
  ADD COLUMN IF NOT EXISTS away_games_played int;

NOTIFY pgrst, 'reload schema';
