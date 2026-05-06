-- NCAAB Phase 1 prep — foundation schema
-- ============================================================
-- Three tables that mirror the NBA pattern, ready for activation
-- when regular season starts in November 2026.
--
--   ncaab_team_aliases    — canonical team name + KenPom + Odds API + alts
--   ncaab_team_stats      — daily KenPom efficiency snapshot per team
--   ncaab_game_results    — per-game state + market lines + outcomes
--
-- Apply via Supabase SQL editor. Idempotent.

-- ────────────────────────────────────────────────────────────
-- 1. Alias table — canonical team name + source-specific names
-- ────────────────────────────────────────────────────────────
-- KenPom uses abbreviated names ("Alabama St.", "St. Mary's").
-- The Odds API uses full mascot names ("Alabama State Hornets",
-- "Saint Mary's Gaels"). Barttorvik mostly matches KenPom.
--
-- canonical_name is what the app surfaces and what other tables
-- foreign-key to. Source columns are unique so a lookup like
-- "given an Odds API team name, what's the canonical?" is direct.

CREATE TABLE IF NOT EXISTS ncaab_team_aliases (
  canonical_name TEXT PRIMARY KEY,
  kenpom_name    TEXT UNIQUE,
  odds_api_name  TEXT UNIQUE,
  bart_name      TEXT UNIQUE,
  alt_names      JSONB DEFAULT '[]'::jsonb,    -- additional spellings (e.g. "St. Mary's", "Saint Mary's")
  conference     TEXT,
  espn_id        TEXT,
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  updated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ncaab_aliases_kenpom ON ncaab_team_aliases(kenpom_name);
CREATE INDEX IF NOT EXISTS idx_ncaab_aliases_odds ON ncaab_team_aliases(odds_api_name);

-- ────────────────────────────────────────────────────────────
-- 2. Team stats — daily KenPom snapshot per team
-- ────────────────────────────────────────────────────────────
-- Mirrors nba_team_stats. One row per team per season, upserted on
-- (team, season). Server-side ncaab_pipeline.py replaces the
-- client-side KenPom fetch (which exposes the API key in the bundle).

CREATE TABLE IF NOT EXISTS ncaab_team_stats (
  team           TEXT NOT NULL,                   -- canonical name
  season         TEXT NOT NULL,                   -- "2025-26"
  conference     TEXT,
  -- KenPom efficiency
  adj_oe         NUMERIC,                         -- offensive efficiency
  adj_de         NUMERIC,                         -- defensive efficiency
  adj_em         NUMERIC,                         -- net efficiency margin
  adj_oe_rank    INTEGER,
  adj_de_rank    INTEGER,
  tempo          NUMERIC,
  tempo_rank     INTEGER,
  -- Four factors offense
  efg_o          NUMERIC,
  efg_o_rank     INTEGER,
  to_o           NUMERIC,
  to_o_rank      INTEGER,
  or_o           NUMERIC,
  or_o_rank      INTEGER,
  ftr_o          NUMERIC,
  ftr_o_rank     INTEGER,
  -- Four factors defense
  efg_d          NUMERIC,
  efg_d_rank     INTEGER,
  to_d           NUMERIC,
  to_d_rank      INTEGER,
  or_d           NUMERIC,
  or_d_rank      INTEGER,
  ftr_d          NUMERIC,
  ftr_d_rank     INTEGER,
  -- Record
  wins           INTEGER,
  losses         INTEGER,
  luck           NUMERIC,
  sos            NUMERIC,
  seed           INTEGER,                         -- tournament seed (NCAA Tourney)
  coach          TEXT,
  updated_at     TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (team, season)
);

CREATE INDEX IF NOT EXISTS idx_ncaab_team_stats_season ON ncaab_team_stats(season DESC);

-- ────────────────────────────────────────────────────────────
-- 3. Game results — pick-time state + market lines + outcomes
-- ────────────────────────────────────────────────────────────
-- Mirrors nba_game_results. ncaab_pick_logger.py (Phase 2, Nov)
-- writes here daily. Resolver fills scores after games complete.

CREATE TABLE IF NOT EXISTS ncaab_game_results (
  game_id            TEXT PRIMARY KEY,
  game_date          DATE,
  season             TEXT,
  home_team          TEXT,                        -- canonical name
  away_team          TEXT,
  home_score         INTEGER,
  away_score         INTEGER,
  home_win           BOOLEAN,
  total_points       INTEGER,
  -- KenPom-derived features at pick time
  home_adj_em        NUMERIC,
  away_adj_em        NUMERIC,
  adj_em_gap         NUMERIC,                     -- home - away
  home_adj_oe        NUMERIC,
  away_adj_oe        NUMERIC,
  home_adj_de        NUMERIC,
  away_adj_de        NUMERIC,
  pace_avg           NUMERIC,                     -- avg of both team tempos
  projected_total    NUMERIC,                     -- our model's total
  projected_spread   NUMERIC,                     -- our model's spread (positive = home wins by X)
  -- Market lines (open + close, same lock semantics as MLB/NBA)
  open_spread        NUMERIC,
  close_spread       NUMERIC,
  open_total         NUMERIC,
  close_total        NUMERIC,
  home_ml_open       INTEGER,
  away_ml_open       INTEGER,
  home_ml_close      INTEGER,
  away_ml_close      INTEGER,
  -- Outcomes (filled by resolver)
  spread_result      TEXT,                        -- "home_covered" | "away_covered" | "push"
  total_result       TEXT,                        -- "over" | "under" | "push"
  result_logged_at   TIMESTAMPTZ,
  created_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ncaab_game_results_date ON ncaab_game_results(game_date DESC);
CREATE INDEX IF NOT EXISTS idx_ncaab_game_results_unresolved
  ON ncaab_game_results(game_date) WHERE home_score IS NULL;
