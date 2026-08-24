-- NFL Madden ratings + Top 100 (2026-08-24) — talent priors for NFL model.
--
-- WHY THIS EXISTS
-- ───────────────
-- Weeks 1-3 EPA sample is thin and market is narrative-driven. Madden's
-- official 32-team OVR/OFF/DEF ratings + the peer-voted Top 100 give us
-- structured roster-talent priors on Day 1 of the season, filling the
-- gap until real EPA stabilizes ~Week 3-4.
--
-- See [[project_madden_top100_nfl_signal_824]] for full spec + reasoning.
--
-- STRUCTURE
-- ─────────
-- 3 tables:
--   nfl_madden_ratings         — team-level snapshot per week
--   nfl_madden_player_ratings  — player-level snapshot per week (~2,362 rows)
--   nfl_top100_snapshot        — NFL Top 100 peer-voted list per season
--
-- 8 new ctx columns on nfl_game_context so signals read via ctx.field
-- rather than JOINing at scoring time.
--
-- All snapshots keyed on week for delta tracking (biggest riser/faller
-- becomes its own signal). Shadow-mode signals in companion migration
-- 20260824c_nfl_madden_signals.sql.

-- ─────────────────────────────────────────────────────────────
-- Team-level ratings
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.nfl_madden_ratings (
  team              TEXT NOT NULL,           -- canonical name (matches nfl_game_context.home_team)
  season            INT  NOT NULL,           -- 2026, 2027, etc.
  week_snapshot     INT  NOT NULL,           -- 0 = launch, 1-18 = in-season updates
  ovr               NUMERIC,                 -- 0-99 overall
  off_rating        NUMERIC,                 -- 0-99 offense
  def_rating        NUMERIC,                 -- 0-99 defense
  ovr_rank          INT,                     -- 1-32 rank within league
  source            TEXT DEFAULT 'ea',       -- ea / madden27.wiki / manual
  fetched_at        TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (team, season, week_snapshot)
);

CREATE INDEX IF NOT EXISTS idx_nfl_madden_ratings_season_week
  ON public.nfl_madden_ratings (season, week_snapshot);

-- ─────────────────────────────────────────────────────────────
-- Player-level ratings
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.nfl_madden_player_ratings (
  player_name       TEXT NOT NULL,
  team              TEXT NOT NULL,
  season            INT  NOT NULL,
  week_snapshot     INT  NOT NULL,
  position          TEXT,                    -- QB / HB / WR / TE / LT / RT / OG / C / DE / DT / LB / CB / S / K / P
  position_group    TEXT,                    -- offense / defense / special
  ovr               NUMERIC,
  speed             NUMERIC,
  awareness         NUMERIC,
  strength          NUMERIC,
  agility           NUMERIC,
  injury            NUMERIC,
  jersey_number     INT,
  source            TEXT DEFAULT 'ea',
  fetched_at        TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (player_name, team, season, week_snapshot)
);

CREATE INDEX IF NOT EXISTS idx_nfl_madden_player_team_season
  ON public.nfl_madden_player_ratings (team, season, week_snapshot);
CREATE INDEX IF NOT EXISTS idx_nfl_madden_player_position
  ON public.nfl_madden_player_ratings (position, season, week_snapshot);
CREATE INDEX IF NOT EXISTS idx_nfl_madden_player_ovr_top
  ON public.nfl_madden_player_ratings (season, week_snapshot, ovr DESC)
  WHERE ovr >= 90;

-- ─────────────────────────────────────────────────────────────
-- NFL Top 100 (peer-voted, annual)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.nfl_top100_snapshot (
  player_name       TEXT NOT NULL,
  team              TEXT,
  season            INT  NOT NULL,
  rank              INT  NOT NULL CHECK (rank BETWEEN 1 AND 100),
  position          TEXT,
  fetched_at        TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (player_name, season)
);

CREATE INDEX IF NOT EXISTS idx_nfl_top100_season_rank
  ON public.nfl_top100_snapshot (season, rank);
CREATE INDEX IF NOT EXISTS idx_nfl_top100_team_season
  ON public.nfl_top100_snapshot (team, season);

-- ─────────────────────────────────────────────────────────────
-- Context columns on nfl_game_context
-- ─────────────────────────────────────────────────────────────
-- Enricher writes these post-scrape so signals read via ctx.
-- All additive & nullable → safe to leave NULL for games without data.

ALTER TABLE public.nfl_game_context
  -- Team OVR/OFF/DEF raw values
  ADD COLUMN IF NOT EXISTS home_madden_ovr           NUMERIC,
  ADD COLUMN IF NOT EXISTS away_madden_ovr           NUMERIC,
  ADD COLUMN IF NOT EXISTS home_madden_off           NUMERIC,
  ADD COLUMN IF NOT EXISTS away_madden_off           NUMERIC,
  ADD COLUMN IF NOT EXISTS home_madden_def           NUMERIC,
  ADD COLUMN IF NOT EXISTS away_madden_def           NUMERIC,
  -- Matchup gaps (signals prefer these — normalized directionality)
  ADD COLUMN IF NOT EXISTS madden_ovr_gap_home       NUMERIC,  -- home_ovr - away_ovr (positive = home better)
  ADD COLUMN IF NOT EXISTS madden_off_gap_home       NUMERIC,  -- home_off - away_def (positive = home offense has leverage)
  ADD COLUMN IF NOT EXISTS madden_off_gap_away       NUMERIC,  -- away_off - home_def
  -- QB matchup (biggest single-player edge in football)
  ADD COLUMN IF NOT EXISTS home_qb_madden_ovr        NUMERIC,
  ADD COLUMN IF NOT EXISTS away_qb_madden_ovr        NUMERIC,
  ADD COLUMN IF NOT EXISTS madden_qb_delta_home      NUMERIC,  -- home_qb - away_qb (positive = home QB better)
  -- Top 100 counts + QB Top 10 flags
  ADD COLUMN IF NOT EXISTS home_top100_count         INT,
  ADD COLUMN IF NOT EXISTS away_top100_count         INT,
  ADD COLUMN IF NOT EXISTS home_qb_top10_flag        BOOLEAN,  -- home QB in Top 10
  ADD COLUMN IF NOT EXISTS away_qb_top10_flag        BOOLEAN;

-- ─────────────────────────────────────────────────────────────
-- RLS — read all, write via service key (matches project convention)
-- ─────────────────────────────────────────────────────────────
DO $$
BEGIN
  ALTER TABLE public.nfl_madden_ratings         ENABLE ROW LEVEL SECURITY;
  ALTER TABLE public.nfl_madden_player_ratings  ENABLE ROW LEVEL SECURITY;
  ALTER TABLE public.nfl_top100_snapshot        ENABLE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

DO $$
BEGIN
  DROP POLICY IF EXISTS "madden_team select all" ON public.nfl_madden_ratings;
  CREATE POLICY "madden_team select all" ON public.nfl_madden_ratings
    FOR SELECT USING (true);
  DROP POLICY IF EXISTS "madden_team write" ON public.nfl_madden_ratings;
  CREATE POLICY "madden_team write" ON public.nfl_madden_ratings
    FOR ALL USING (true) WITH CHECK (true);

  DROP POLICY IF EXISTS "madden_player select all" ON public.nfl_madden_player_ratings;
  CREATE POLICY "madden_player select all" ON public.nfl_madden_player_ratings
    FOR SELECT USING (true);
  DROP POLICY IF EXISTS "madden_player write" ON public.nfl_madden_player_ratings;
  CREATE POLICY "madden_player write" ON public.nfl_madden_player_ratings
    FOR ALL USING (true) WITH CHECK (true);

  DROP POLICY IF EXISTS "top100 select all" ON public.nfl_top100_snapshot;
  CREATE POLICY "top100 select all" ON public.nfl_top100_snapshot
    FOR SELECT USING (true);
  DROP POLICY IF EXISTS "top100 write" ON public.nfl_top100_snapshot;
  CREATE POLICY "top100 write" ON public.nfl_top100_snapshot
    FOR ALL USING (true) WITH CHECK (true);
END $$;

NOTIFY pgrst, 'reload schema';
