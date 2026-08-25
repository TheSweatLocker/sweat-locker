-- NBA v1.0 foundation (2026-08-17).
--
-- Full reset from BDL-based dead code. Fresh schema for stats via ESPN
-- API + Odds API for market lines. Mirrors NHL v1.0 pattern.
--
-- Season starts Oct 22, 2026 — 9 weeks lead time.
--
-- ── PRE-CLEANUP (2026-08-17): drop stale BDL-era tables ─────────────
-- The dead nba_pick_logger + nba_pipeline scripts wrote to tables with
-- different schemas (BDL-style team names, no ATS columns). Since those
-- scripts were deleted and no downstream reads exist, dropping is safe.
-- CASCADE handles any incidental foreign-key or view dependencies.

DROP TABLE IF EXISTS public.nba_game_context CASCADE;
DROP TABLE IF EXISTS public.nba_game_results CASCADE;
DROP TABLE IF EXISTS public.nba_injuries CASCADE;
DROP TABLE IF EXISTS public.nba_team_stats CASCADE;
DROP TABLE IF EXISTS public.nba_player_game_logs CASCADE;

CREATE TABLE IF NOT EXISTS public.nba_game_context (
  game_id                       TEXT PRIMARY KEY,
  game_date                     DATE NOT NULL,
  season                        TEXT,                  -- '2025-26'
  season_type                   TEXT,                  -- 'preseason' | 'regular' | 'playoffs'
  commence_time_utc             TIMESTAMPTZ,
  -- Teams (ESPN naming: full display names)
  home_team                     TEXT NOT NULL,
  away_team                     TEXT NOT NULL,
  home_abbrev                   TEXT,
  away_abbrev                   TEXT,
  home_team_id                  TEXT,
  away_team_id                  TEXT,
  home_record                   TEXT,                  -- '9-2' etc.
  away_record                   TEXT,
  venue                         TEXT,
  neutral_site                  BOOLEAN DEFAULT false,
  -- Market lines (from Odds API)
  close_spread                  NUMERIC,
  close_total                   NUMERIC,
  home_ml_close                 INT,
  away_ml_close                 INT,
  open_spread                   NUMERIC,
  open_total                    NUMERIC,
  -- Team season stats (populated by nba_game_context.py)
  home_off_rating               NUMERIC,
  home_def_rating               NUMERIC,
  home_net_rating               NUMERIC,
  home_pace                     NUMERIC,
  away_off_rating               NUMERIC,
  away_def_rating               NUMERIC,
  away_net_rating               NUMERIC,
  away_pace                     NUMERIC,
  -- Rest / back-to-back
  home_rest_days                INT,
  away_rest_days                INT,
  home_is_b2b                   BOOLEAN,
  away_is_b2b                   BOOLEAN,
  -- Injury impact (starter out flags — populated by injury_scraper)
  home_injury_impact            NUMERIC,               -- -1.0 to 0 magnitude
  away_injury_impact            NUMERIC,
  home_starters_out             TEXT[],                -- array of player names
  away_starters_out             TEXT[],
  -- Model projections (filled by ensemble scorer)
  projected_spread              NUMERIC,
  projected_total               NUMERIC,
  -- Ensemble output
  primary_play                  JSONB,
  -- L10 team tendency fields (populated by backfill_nba_team_tendencies)
  home_ats_last10               INT,
  home_ats_last10_losses        INT,
  away_ats_last10               INT,
  away_ats_last10_losses        INT,
  home_ou_last10_overs          INT,
  home_ou_last10_unders         INT,
  away_ou_last10_overs          INT,
  away_ou_last10_unders         INT,
  home_covers_as_fav_pct        NUMERIC,
  home_covers_as_dog_pct        NUMERIC,
  away_covers_as_fav_pct        NUMERIC,
  away_covers_as_dog_pct        NUMERIC,
  home_ml_last10                INT,
  home_ml_last10_losses         INT,
  away_ml_last10                INT,
  away_ml_last10_losses         INT,
  team_tendencies_updated_at    TIMESTAMPTZ,
  -- Season trends (populated by enrich_team_trends.py from team_season_trends)
  home_season_cover_pct         NUMERIC,
  home_season_ats_wins          INT,
  home_season_ats_losses        INT,
  home_season_over_pct          NUMERIC,
  home_season_ou_overs          INT,
  home_season_ou_unders         INT,
  away_season_cover_pct         NUMERIC,
  away_season_ats_wins          INT,
  away_season_ats_losses        INT,
  away_season_over_pct          NUMERIC,
  away_season_ou_overs          INT,
  away_season_ou_unders         INT,
  team_trends_updated_at        TIMESTAMPTZ,
  -- Audit timestamps
  created_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nba_ctx_date ON public.nba_game_context (game_date DESC);
CREATE INDEX IF NOT EXISTS idx_nba_ctx_teams ON public.nba_game_context (home_team, away_team, game_date);

CREATE TABLE IF NOT EXISTS public.nba_game_results (
  game_id           TEXT PRIMARY KEY,
  game_date         DATE NOT NULL,
  season            TEXT,
  home_team         TEXT NOT NULL,
  away_team         TEXT NOT NULL,
  home_abbrev       TEXT,
  away_abbrev       TEXT,
  home_score        INT,
  away_score        INT,
  total_points      INT,
  home_win          BOOLEAN,
  went_to_ot        BOOLEAN,
  -- Copied from ctx for grading self-containment
  close_spread      NUMERIC,
  close_total       NUMERIC,
  close_home_ml     INT,
  close_away_ml     INT,
  -- Computed by resolver
  spread_result     TEXT,      -- 'home_covered' | 'away_covered' | 'push'
  total_result      TEXT,      -- 'over' | 'under' | 'push'
  resolved_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nba_results_date ON public.nba_game_results (game_date DESC);

-- Injuries (scraped daily — ESPN roster status + Rotowire)
CREATE TABLE IF NOT EXISTS public.nba_injuries (
  id            BIGSERIAL PRIMARY KEY,
  team_abbrev   TEXT NOT NULL,
  player_name   TEXT NOT NULL,
  status        TEXT,               -- 'OUT' | 'QUESTIONABLE' | 'PROBABLE' | 'GTD'
  reason        TEXT,               -- 'knee', 'illness', etc.
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(team_abbrev, player_name, updated_at)
);
CREATE INDEX IF NOT EXISTS idx_nba_injuries_team ON public.nba_injuries (team_abbrev, status);

-- Team season stats (four-factor + advanced from Basketball Reference)
CREATE TABLE IF NOT EXISTS public.nba_team_stats (
  team_abbrev       TEXT NOT NULL,
  season            TEXT NOT NULL,
  -- 4 factors
  efg_pct           NUMERIC,          -- effective FG%
  tov_pct           NUMERIC,          -- turnover %
  orb_pct           NUMERIC,          -- offensive rebound %
  ft_rate           NUMERIC,          -- FT/FGA
  -- Def 4 factors
  opp_efg_pct       NUMERIC,
  opp_tov_pct       NUMERIC,
  opp_orb_pct       NUMERIC,
  opp_ft_rate       NUMERIC,
  -- Efficiency
  off_rating        NUMERIC,
  def_rating        NUMERIC,
  net_rating        NUMERIC,
  pace              NUMERIC,
  -- Record
  wins              INT,
  losses            INT,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (team_abbrev, season)
);

-- Player game logs (for prop backtest + L5/L10 lookback)
CREATE TABLE IF NOT EXISTS public.nba_player_game_logs (
  id              BIGSERIAL PRIMARY KEY,
  player_name     TEXT NOT NULL,
  player_id       TEXT,
  team_abbrev     TEXT,
  game_id         TEXT NOT NULL,
  game_date       DATE NOT NULL,
  opp_abbrev      TEXT,
  minutes         NUMERIC,
  points          INT,
  rebounds        INT,
  assists         INT,
  steals          INT,
  blocks          INT,
  turnovers       INT,
  threes_made     INT,
  fg_made         INT,
  fg_att          INT,
  ft_made         INT,
  ft_att          INT,
  plus_minus      INT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (player_name, game_id)
);
CREATE INDEX IF NOT EXISTS idx_nba_plyr_gl_date
  ON public.nba_player_game_logs (player_name, game_date DESC);

-- ─── RLS: match tightened pattern ──────────────────────────────────
DO $$
DECLARE tbl text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['nba_game_context','nba_game_results','nba_injuries','nba_team_stats','nba_player_game_logs'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('DROP POLICY IF EXISTS public_read ON public.%I', tbl);
    EXECUTE format('CREATE POLICY public_read ON public.%I FOR SELECT TO anon, authenticated USING (true)', tbl);
    EXECUTE format('DROP POLICY IF EXISTS service_role_write ON public.%I', tbl);
    EXECUTE format('CREATE POLICY service_role_write ON public.%I FOR ALL TO service_role USING (true) WITH CHECK (true)', tbl);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
