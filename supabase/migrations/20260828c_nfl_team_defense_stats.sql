-- NFL team defense stats table (2026-08-28).
--
-- Fills the season-long gap where nfl_team_stats only holds offense-side
-- metrics and defensive event counts (sacks, INTs) — but no yards-allowed
-- pass/rush or points-allowed-per-game. Bettors + Jerry both need these
-- to reason about matchup defense.
--
-- Derived per team from opponent's seasonal offense via
-- nfl_team_defense_backfill.py. Refreshed nightly during season.

CREATE TABLE IF NOT EXISTS nfl_team_defense_stats (
    team              TEXT NOT NULL,
    season            INTEGER NOT NULL,
    season_type       TEXT NOT NULL DEFAULT 'reg',
    games             INTEGER NOT NULL,
    def_ppg           NUMERIC(5, 2),      -- opponent points per game
    def_ypg           NUMERIC(6, 2),      -- opponent total yards per game
    def_pass_ypg      NUMERIC(6, 2),      -- opponent pass yards per game
    def_rush_ypg      NUMERIC(6, 2),      -- opponent rush yards per game
    def_pass_epa_allowed NUMERIC(6, 4),   -- opponent pass EPA per game
    def_rush_epa_allowed NUMERIC(6, 4),   -- opponent rush EPA per game
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team, season, season_type)
);

CREATE INDEX IF NOT EXISTS idx_nfl_team_defense_season
    ON nfl_team_defense_stats (season DESC, def_ppg ASC);

NOTIFY pgrst, 'reload schema';
