-- 2026-07-23 — NCAAF Phase 1 foundation.
--
-- v1.0 scope (per project_ncaaf_scope): Spread / ML / Total only.
-- NO player props. Data source: cfbd-api (CollegeFootballData.com,
-- free tier). Season starts 2026-08-22.
--
-- Mirrors nfl_* schema for pattern consistency. Sport-agnostic
-- resolver/externals/consensus_fade all work by dropping sport='NCAAF'
-- once these tables exist.

-- ─────────────────────────────────────────────────────────────
-- Team aliases (bootstrap seed of ~130 FBS teams)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ncaaf_team_aliases (
  canonical_name    TEXT PRIMARY KEY,          -- CFBD-standard abbrev/short name
  full_name         TEXT NOT NULL,             -- Odds API full name (e.g., "Alabama Crimson Tide")
  location          TEXT,                       -- "Alabama"
  nickname          TEXT,                       -- "Crimson Tide"
  conference        TEXT,                       -- "SEC" / "Big Ten" / "Big 12" / "ACC" / "Pac-12" / "AAC" / etc.
  division          TEXT,                       -- "FBS" — future ncaab uses this pattern
  classification    TEXT DEFAULT 'FBS',         -- FBS / FCS (only FBS in v1)
  alt_names         TEXT[],
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ncaaf_aliases_conference
  ON ncaaf_team_aliases (conference);

-- ─────────────────────────────────────────────────────────────
-- Team season stats
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ncaaf_team_stats (
  team              TEXT NOT NULL,
  season            INT NOT NULL,
  season_type       TEXT NOT NULL DEFAULT 'regular',  -- regular / postseason
  games             INT,
  -- Offensive EPA
  off_epa_per_play  NUMERIC,
  off_pass_epa      NUMERIC,
  off_rush_epa      NUMERIC,
  off_success_rate  NUMERIC,
  off_explosiveness NUMERIC,
  -- Defensive EPA
  def_epa_per_play  NUMERIC,
  def_pass_epa      NUMERIC,
  def_rush_epa      NUMERIC,
  def_success_rate  NUMERIC,
  -- Special teams
  st_epa_per_play   NUMERIC,
  -- SP+ ratings (CFBD advanced)
  sp_overall        NUMERIC,
  sp_offense        NUMERIC,
  sp_defense        NUMERIC,
  -- Updated
  updated_at        TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (team, season, season_type)
);

-- ─────────────────────────────────────────────────────────────
-- Game results (schedules + outcomes + market lines)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ncaaf_game_results (
  game_id           TEXT PRIMARY KEY,           -- CFBD id or Odds API event id
  season            INT NOT NULL,
  season_type       TEXT DEFAULT 'regular',     -- regular / postseason
  week              INT,
  game_date         DATE NOT NULL,
  kickoff_utc       TIMESTAMPTZ,
  home_team         TEXT NOT NULL,
  away_team         TEXT NOT NULL,
  neutral_site      BOOLEAN,
  conference_game   BOOLEAN,
  -- Market lines (Odds API, home-team perspective)
  open_spread       NUMERIC,
  close_spread      NUMERIC,                    -- positive = home dog (MLB-like convention for CFB)
  open_total        NUMERIC,
  close_total       NUMERIC,
  open_home_ml      INT,
  close_home_ml     INT,
  open_away_ml      INT,
  close_away_ml     INT,
  -- Venue
  venue             TEXT,
  city              TEXT,
  state             TEXT,
  temp              INT,
  wind              INT,
  -- Outcome
  home_score        INT,
  away_score        INT,
  total_points      INT,
  home_win          BOOLEAN,
  overtime          BOOLEAN,
  spread_result     TEXT,                       -- home_covered / away_covered / push
  total_result      TEXT,                       -- over / under / push
  attendance        INT,
  -- Tracking
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ncaaf_results_season_week
  ON ncaaf_game_results (season, week);
CREATE INDEX IF NOT EXISTS idx_ncaaf_results_date
  ON ncaaf_game_results (game_date DESC);
CREATE INDEX IF NOT EXISTS idx_ncaaf_results_teams
  ON ncaaf_game_results (home_team, away_team);

-- ─────────────────────────────────────────────────────────────
-- Game context (pick-time pre-game snapshot)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ncaaf_game_context (
  game_id                       TEXT PRIMARY KEY,
  game_date                     DATE NOT NULL,
  season                        INT,
  season_type                   TEXT DEFAULT 'regular',
  week                          INT,
  home_team                     TEXT NOT NULL,
  away_team                     TEXT NOT NULL,
  kickoff_utc                   TIMESTAMPTZ,
  -- Market
  close_spread                  NUMERIC,
  open_spread                   NUMERIC,
  close_total                   NUMERIC,
  open_total                    NUMERIC,
  close_home_ml                 INT,
  close_away_ml                 INT,
  -- Rest/Travel
  neutral_site                  BOOLEAN,
  conference_game               BOOLEAN,
  -- Model projections (EPA + SP+ based)
  home_off_epa_pp               NUMERIC,
  away_off_epa_pp               NUMERIC,
  home_def_epa_pp               NUMERIC,
  away_def_epa_pp               NUMERIC,
  home_sp_overall               NUMERIC,
  away_sp_overall               NUMERIC,
  sp_gap                        NUMERIC,          -- home_sp - away_sp
  projected_spread              NUMERIC,          -- positive = home fav
  projected_total               NUMERIC,
  model_pred_home_points        NUMERIC,
  model_pred_away_points        NUMERIC,
  -- Confluence
  signal_confluence_net         INT,
  signal_confluence_breakdown   JSONB,
  cohort_tags                   TEXT[],
  -- Sweat + primary play
  sweat_score                   INT,
  sweat_tier                    TEXT,
  primary_play                  JSONB,
  -- Timestamps
  computed_at                   TIMESTAMPTZ DEFAULT NOW(),
  updated_at                    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ncaaf_context_date
  ON ncaaf_game_context (game_date DESC);
CREATE INDEX IF NOT EXISTS idx_ncaaf_context_week
  ON ncaaf_game_context (season, week);

-- ─────────────────────────────────────────────────────────────
-- RLS: pipeline writes via anon (SUPABASE_KEY)
-- ─────────────────────────────────────────────────────────────
DO $$
BEGIN
  ALTER TABLE ncaaf_team_aliases    ENABLE ROW LEVEL SECURITY;
  ALTER TABLE ncaaf_team_stats      ENABLE ROW LEVEL SECURITY;
  ALTER TABLE ncaaf_game_results    ENABLE ROW LEVEL SECURITY;
  ALTER TABLE ncaaf_game_context    ENABLE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

DO $$
BEGIN
  DROP POLICY IF EXISTS "ncaaf_aliases select all" ON ncaaf_team_aliases;
  CREATE POLICY "ncaaf_aliases select all" ON ncaaf_team_aliases FOR SELECT USING (true);
  DROP POLICY IF EXISTS "ncaaf_aliases write anon" ON ncaaf_team_aliases;
  CREATE POLICY "ncaaf_aliases write anon" ON ncaaf_team_aliases FOR ALL USING (true) WITH CHECK (true);

  DROP POLICY IF EXISTS "ncaaf_stats select all" ON ncaaf_team_stats;
  CREATE POLICY "ncaaf_stats select all" ON ncaaf_team_stats FOR SELECT USING (true);
  DROP POLICY IF EXISTS "ncaaf_stats write anon" ON ncaaf_team_stats;
  CREATE POLICY "ncaaf_stats write anon" ON ncaaf_team_stats FOR ALL USING (true) WITH CHECK (true);

  DROP POLICY IF EXISTS "ncaaf_results select all" ON ncaaf_game_results;
  CREATE POLICY "ncaaf_results select all" ON ncaaf_game_results FOR SELECT USING (true);
  DROP POLICY IF EXISTS "ncaaf_results write anon" ON ncaaf_game_results;
  CREATE POLICY "ncaaf_results write anon" ON ncaaf_game_results FOR ALL USING (true) WITH CHECK (true);

  DROP POLICY IF EXISTS "ncaaf_context select all" ON ncaaf_game_context;
  CREATE POLICY "ncaaf_context select all" ON ncaaf_game_context FOR SELECT USING (true);
  DROP POLICY IF EXISTS "ncaaf_context write anon" ON ncaaf_game_context;
  CREATE POLICY "ncaaf_context write anon" ON ncaaf_game_context FOR ALL USING (true) WITH CHECK (true);
END $$;

NOTIFY pgrst, 'reload schema';
