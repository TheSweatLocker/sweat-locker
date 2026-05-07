-- NFL Phase 1 prep — foundation schema
-- ============================================================
-- Three tables that mirror NBA + NCAAB patterns, ready for activation
-- when 2026 NFL preseason starts (Aug) and full launch Week 1 (Sept 4).
--
--   nfl_team_aliases   — 32 teams with abbreviation + Odds API + alt names
--   nfl_team_stats     — season-aggregate stats per team (mirrors NBA pattern)
--   nfl_game_results   — per-game pick-time state + market lines + outcomes
--                        (matches mlb_game_results / nba_game_results pattern)
--
-- Phase 2 (June-July): nfl_player_stats + nfl_props tables
--
-- Apply via Supabase SQL editor. Idempotent.

-- ────────────────────────────────────────────────────────────
-- 1. Alias table — 32 NFL teams with name variants
-- ────────────────────────────────────────────────────────────
-- nflverse uses 3-letter abbreviations (KC, PHI, NE, SF, NYJ, NYG, etc).
-- Odds API uses full names ("Kansas City Chiefs", "Philadelphia Eagles").
-- ESPN often uses location ("Kansas City"), some media use mascot only.
-- canonical_name = nflverse abbreviation (stable across years).

CREATE TABLE IF NOT EXISTS nfl_team_aliases (
  canonical_name  TEXT PRIMARY KEY,           -- nflverse abbrev (KC, PHI, NE)
  full_name       TEXT,                       -- "Kansas City Chiefs"
  city            TEXT,                       -- "Kansas City"
  mascot          TEXT,                       -- "Chiefs"
  odds_api_name   TEXT UNIQUE,                -- as Odds API returns it
  espn_name       TEXT,                       -- ESPN's preferred display
  conference      TEXT,                       -- AFC / NFC
  division        TEXT,                       -- AFC East / NFC West / etc
  alt_names       JSONB DEFAULT '[]'::jsonb,  -- additional spellings
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nfl_aliases_odds ON nfl_team_aliases(odds_api_name);
CREATE INDEX IF NOT EXISTS idx_nfl_aliases_full ON nfl_team_aliases(full_name);

-- ────────────────────────────────────────────────────────────
-- 2. Team stats — season-aggregate per team
-- ────────────────────────────────────────────────────────────
-- Mirrors nba_team_stats. One row per team per season, upserted on (team, season).
-- Source: nflverse stats_team release (regular season + post-season variants).
-- Server-side nfl_pipeline.py pulls weekly during season, in-season cron.

CREATE TABLE IF NOT EXISTS nfl_team_stats (
  team             TEXT NOT NULL,             -- canonical (KC, PHI, etc)
  season           INT NOT NULL,              -- 2025, 2026
  season_type      TEXT NOT NULL DEFAULT 'REG', -- 'REG' | 'POST'
  games            INT,
  -- Passing (offense)
  pass_attempts    INT,
  pass_completions INT,
  pass_yards       INT,
  pass_tds         INT,
  pass_ints        INT,
  pass_epa         NUMERIC,                   -- expected points added on passing plays
  pass_cpoe        NUMERIC,                   -- completion % over expected (premium signal)
  pass_air_yards   INT,
  pass_yac         INT,                       -- yards after catch
  sacks_suffered   INT,
  -- Rushing (offense)
  rush_attempts    INT,
  rush_yards       INT,
  rush_tds         INT,
  rush_epa         NUMERIC,
  rush_first_downs INT,
  -- Receiving (team total = pass yards by recipients)
  receiving_epa    NUMERIC,                   -- separate from pass_epa, focuses on YAC + target quality
  -- Defense
  def_sacks        INT,
  def_ints         INT,
  def_pass_def     INT,                       -- passes defended
  def_fumbles_forced INT,
  def_tds          INT,
  -- Penalties / discipline
  penalties        INT,
  penalty_yards    INT,
  -- Special teams
  fg_made          INT,
  fg_att           INT,
  fg_pct           NUMERIC,
  fg_long          INT,
  -- Updated
  updated_at       TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (team, season, season_type)
);

CREATE INDEX IF NOT EXISTS idx_nfl_team_stats_season ON nfl_team_stats(season DESC, season_type);

-- ────────────────────────────────────────────────────────────
-- 3. Game results — pick-time state + market lines + outcomes
-- ────────────────────────────────────────────────────────────
-- Mirrors mlb_game_results / nba_game_results.
-- Pre-game features captured at logger time, outcomes filled by resolver.
-- Audit cohorts read from this table for hit-rate calibration.

CREATE TABLE IF NOT EXISTS nfl_game_results (
  game_id              TEXT PRIMARY KEY,           -- nflverse game_id (2025_01_DAL_PHI)
  season               INT,
  week                 INT,
  game_type            TEXT,                       -- 'REG' | 'POST' | 'WC' | 'DIV' | 'CON' | 'SB'
  game_date            DATE,
  weekday              TEXT,                       -- 'Sunday', 'Thursday', 'Monday'
  gametime             TEXT,                       -- '20:20' (kickoff time ET)
  home_team            TEXT,                       -- canonical abbrev
  away_team            TEXT,
  -- Market lines (pre-game capture)
  open_spread          NUMERIC,                    -- home spread (negative = home favored)
  close_spread         NUMERIC,
  open_total           NUMERIC,
  close_total          NUMERIC,
  open_home_ml         INT,
  close_home_ml        INT,
  open_away_ml         INT,
  close_away_ml        INT,
  -- Game environment
  stadium              TEXT,
  roof                 TEXT,                       -- 'outdoors' | 'dome' | 'closed' | 'open'
  surface              TEXT,                       -- 'grass' | 'turf'
  temp                 INT,                        -- gametime temp (F)
  wind                 INT,                        -- gametime wind speed (mph)
  div_game             BOOLEAN,
  -- Situational features
  home_rest            INT,                        -- days rest
  away_rest            INT,
  -- Pitcher/QB-equivalent: starting QB
  home_qb_id           TEXT,
  away_qb_id           TEXT,
  home_qb_name         TEXT,
  away_qb_name         TEXT,
  home_coach           TEXT,
  away_coach           TEXT,
  referee              TEXT,
  -- Outcomes (filled by resolver)
  home_score           INT,
  away_score           INT,
  total_points         INT,                        -- home + away
  home_win             BOOLEAN,
  overtime             BOOLEAN,
  spread_result        TEXT,                       -- 'home_covered' | 'away_covered' | 'push'
  total_result         TEXT,                       -- 'over' | 'under' | 'push'
  result_logged_at     TIMESTAMPTZ,
  created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nfl_game_results_season_week ON nfl_game_results(season DESC, week);
CREATE INDEX IF NOT EXISTS idx_nfl_game_results_date ON nfl_game_results(game_date DESC);
CREATE INDEX IF NOT EXISTS idx_nfl_game_results_unresolved
  ON nfl_game_results(game_date) WHERE home_score IS NULL;
