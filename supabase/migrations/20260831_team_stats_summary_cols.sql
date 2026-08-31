-- 2026-08-31: Team-stats summary blobs for NFL + NCAAF game cards.
--
-- Rationale: nfl_team_stats / ncaaf_stats hold rich per-season data
-- (rush_yards, pass_yards, defense, penalties) but only 2 derived
-- fields ever flowed into game_context (off_rating, def_rating).
-- Game cards can't render casual-friendly numbers like "Iowa State
-- averages 189 rush yds/g" without a schema pathway.
--
-- Solution: JSONB summary blob per side, computed at build_row time
-- from the rich stats table. App reads the blob and renders a "Team
-- Matchup" block. No per-stat column proliferation on game_context.
--
-- Blob shape (both sports):
--   {
--     "pts_pg": 28.3, "pts_allowed_pg": 21.1,
--     "rush_yds_pg": 132, "rush_yds_allowed_pg": 108,
--     "pass_yds_pg": 265, "pass_yds_allowed_pg": 224,
--     "turnover_diff_pg": 0.6, "sacks_pg": 2.9,
--     "rank_off": 8, "rank_def": 14,
--     "rank_rush_off": 5, "rank_rush_def": 22,
--     "rank_pass_off": 12, "rank_pass_def": 18,
--     "season_source": 2025,       -- which season the stats came from
--     "games_sample": 17
--   }
--
-- NCAAF blob adds: sp_plus_off_rank, sp_plus_def_rank, srs, ap_rank
-- when available (dropped when null so keys stay compact).

ALTER TABLE nfl_game_context
    ADD COLUMN IF NOT EXISTS home_team_stats_summary jsonb,
    ADD COLUMN IF NOT EXISTS away_team_stats_summary jsonb;

ALTER TABLE ncaaf_game_context
    ADD COLUMN IF NOT EXISTS home_team_stats_summary jsonb,
    ADD COLUMN IF NOT EXISTS away_team_stats_summary jsonb;

-- PostgREST needs a schema-cache reload to see the new columns
-- (per feedback_migration_pgrst_reload memory).
NOTIFY pgrst, 'reload schema';
