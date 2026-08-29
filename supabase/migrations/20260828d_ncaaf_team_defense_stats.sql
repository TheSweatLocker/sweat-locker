-- NCAAF team defense stats table (2026-08-28).
--
-- Fills the gap where ncaaf_team_stats only tracks EPA-based efficiency
-- (def_pass_epa, def_rush_epa, def_success_rate) — no points-allowed
-- or raw yards-allowed. Adds derived opponent-perspective stats.
--
-- NOTE on yards: ncaaf_team_stats doesn't carry raw pass_yards/rush_yards
-- (CFBD advanced-stats endpoint returns EPA-based only). We store
-- points-allowed + EPA-based derivations here for now. Raw yards
-- backfill from CFBD /stats/season (non-advanced) is a follow-up.
--
-- Derived from opponent EPA via ncaaf_team_defense_backfill.py.

CREATE TABLE IF NOT EXISTS ncaaf_team_defense_stats (
    team                    TEXT NOT NULL,
    season                  INTEGER NOT NULL,
    season_type             TEXT NOT NULL DEFAULT 'regular',
    games                   INTEGER NOT NULL,
    def_ppg                 NUMERIC(5, 2),   -- opponent points per game
    def_pass_epa_allowed    NUMERIC(6, 4),   -- opponent pass EPA/play, seasonal avg
    def_rush_epa_allowed    NUMERIC(6, 4),   -- opponent rush EPA/play, seasonal avg
    def_success_rate_allowed NUMERIC(5, 4),  -- opponent success rate, seasonal avg
    def_explosiveness_allowed NUMERIC(5, 4), -- opponent explosiveness, seasonal avg
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team, season, season_type)
);

CREATE INDEX IF NOT EXISTS idx_ncaaf_team_defense_season
    ON ncaaf_team_defense_stats (season DESC, def_ppg ASC);

NOTIFY pgrst, 'reload schema';
