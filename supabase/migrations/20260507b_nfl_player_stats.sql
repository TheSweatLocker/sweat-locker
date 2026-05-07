-- NFL Phase 2 — player-week stats schema
-- ============================================================
-- Per-player per-week granularity for QB/RB/WR/TE prop modeling. Source:
-- nflverse player_stats release (~5,500 rows per season covering all roster
-- players who recorded a stat). Updated weekly during in-season cron.
--
-- Used by Phase 2 props pipeline (June-July build) to compute rolling
-- L4 / L8 / season averages for each prop type:
--   - Passing yards O/U → use rolling L4 + opp_team passing EPA allowed
--   - Rushing yards O/U → carries × YPC trend + opp run defense
--   - Receiving yards O/U → target_share + air_yards_share + opp coverage
--   - Anytime TD → red-zone target share + score game-script projection
--
-- Apply via Supabase SQL editor.

CREATE TABLE IF NOT EXISTS nfl_player_stats (
  player_id            TEXT NOT NULL,
  player_name          TEXT NOT NULL,
  position             TEXT,                       -- QB, RB, WR, TE, FB
  position_group       TEXT,
  team                 TEXT,                       -- canonical abbrev
  season               INT NOT NULL,
  week                 INT NOT NULL,
  season_type          TEXT NOT NULL DEFAULT 'REG', -- REG | POST
  opponent_team        TEXT,
  -- Passing
  completions          INT,
  attempts             INT,
  passing_yards        INT,
  passing_tds          INT,
  interceptions        INT,
  sacks                INT,
  sack_yards           INT,
  passing_air_yards    INT,
  passing_yards_after_catch INT,
  passing_first_downs  INT,
  passing_epa          NUMERIC,
  pacr                 NUMERIC,                    -- passing air conversion ratio
  dakota               NUMERIC,                    -- composite passing rating (EPA + CPOE)
  -- Rushing
  carries              INT,
  rushing_yards        INT,
  rushing_tds          INT,
  rushing_first_downs  INT,
  rushing_epa          NUMERIC,
  -- Receiving
  receptions           INT,
  targets              INT,
  receiving_yards      INT,
  receiving_tds        INT,
  receiving_air_yards  INT,
  receiving_yards_after_catch INT,
  receiving_first_downs INT,
  receiving_epa        NUMERIC,
  racr                 NUMERIC,                    -- receiver air conversion ratio
  target_share         NUMERIC,
  air_yards_share      NUMERIC,
  wopr                 NUMERIC,                    -- weighted opportunity rating
  -- Special teams
  special_teams_tds    INT,
  -- Fantasy
  fantasy_points       NUMERIC,
  fantasy_points_ppr   NUMERIC,
  updated_at           TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (player_id, season, week, season_type)
);

CREATE INDEX IF NOT EXISTS idx_nfl_player_stats_team_week ON nfl_player_stats(team, season, week);
CREATE INDEX IF NOT EXISTS idx_nfl_player_stats_position ON nfl_player_stats(position, season DESC, week DESC);
CREATE INDEX IF NOT EXISTS idx_nfl_player_stats_player_recent ON nfl_player_stats(player_id, season DESC, week DESC);

-- ────────────────────────────────────────────────────────────
-- Convenience view: most recent week's stats per player (for rolling averages
-- the puller can join against this rather than re-computing windows in app).
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW nfl_player_recent AS
SELECT DISTINCT ON (player_id) *
FROM nfl_player_stats
WHERE season_type = 'REG'
ORDER BY player_id, season DESC, week DESC;
