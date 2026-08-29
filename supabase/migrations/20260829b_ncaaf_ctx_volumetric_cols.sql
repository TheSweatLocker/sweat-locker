-- Add 36 volumetric + defense-per-game columns to ncaaf_game_context
-- (2026-08-29). Fields are populated by ncaaf_game_context.py from
-- ncaaf_team_stats (backfilled to include penalties/yards/downs) and
-- ncaaf_team_defense_stats (pass_ypg/rush_ypg allowed added earlier).
-- Without these columns the ctx upsert 400s and blocks the whole
-- 106-game rebuild — dynamic-strip loop capped at 20 rounds.

ALTER TABLE ncaaf_game_context
    -- Defense stats (both sides) — 14 cols
    ADD COLUMN IF NOT EXISTS home_def_ppg NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS home_def_pass_ypg NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS home_def_rush_ypg NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS home_def_pass_epa_allowed NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS home_def_rush_epa_allowed NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS home_def_success_rate_allowed NUMERIC(5,4),
    ADD COLUMN IF NOT EXISTS home_def_explosiveness_allowed NUMERIC(5,4),
    ADD COLUMN IF NOT EXISTS away_def_ppg NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS away_def_pass_ypg NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS away_def_rush_ypg NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS away_def_pass_epa_allowed NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS away_def_rush_epa_allowed NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS away_def_success_rate_allowed NUMERIC(5,4),
    ADD COLUMN IF NOT EXISTS away_def_explosiveness_allowed NUMERIC(5,4),
    -- Offense volumetric per game (both sides) — 12 cols
    ADD COLUMN IF NOT EXISTS home_pass_yds_pg NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS home_rush_yds_pg NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS home_pass_tds_pg NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS home_rush_tds_pg NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS away_pass_yds_pg NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS away_rush_yds_pg NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS away_pass_tds_pg NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS away_rush_tds_pg NUMERIC(4,2),
    -- Discipline / situational (both sides) — 10 cols
    ADD COLUMN IF NOT EXISTS home_penalties_pg NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS home_penalty_yds_pg NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS away_penalties_pg NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS away_penalty_yds_pg NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS home_third_down_pct NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS away_third_down_pct NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS home_turnovers_pg NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS away_turnovers_pg NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS home_top_min NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS away_top_min NUMERIC(5,2),
    -- Defense events per game (both sides) — 4 cols
    ADD COLUMN IF NOT EXISTS home_def_sacks_pg NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS home_def_ints_pg NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS away_def_sacks_pg NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS away_def_ints_pg NUMERIC(4,2);

NOTIFY pgrst, 'reload schema';
