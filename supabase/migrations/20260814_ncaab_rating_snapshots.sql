-- NCAAB rating snapshots — multi-source rating store (2026-08-14).
--
-- Session 1 of the NCAAB 5-lens build. Adds a sport-agnostic rating table
-- that stores each rating system's per-team output separately, so the
-- Panel model can aggregate them into a consensus without coupling scrapers.
--
-- Why not extend ncaab_team_stats?
--   ncaab_team_stats is KenPom's schema (adj_oe/adj_de/tempo/four factors)
--   with 33 columns. Adding torvik_adj_em, massey_adj_em, haslam_adj_em
--   would couple 4 scrapers to a single row + create write conflicts.
--   A snapshot table is pluggable — add a rating system = 1 scraper +
--   0 schema changes.
--
-- Reading pattern (Panel model):
--   SELECT team, AVG(adj_em) as panel_em, COUNT(*) as n_systems
--   FROM ncaab_rating_snapshots
--   WHERE season='2025-26' AND snapshot_date=(SELECT MAX(snapshot_date)
--         FROM ncaab_rating_snapshots WHERE season='2025-26')
--   GROUP BY team
--   HAVING n_systems >= 2
--
-- Retention: full history. We want rating drift over time (a team dropping
-- 10 KenPom spots in 2 weeks = signal). Bounded by 365 teams × 4 systems ×
-- ~200 snapshot days/season = 292k rows/season. Trivial for Postgres.

CREATE TABLE IF NOT EXISTS public.ncaab_rating_snapshots (
  id             BIGSERIAL PRIMARY KEY,
  snapshot_date  DATE NOT NULL,
  team           TEXT NOT NULL,      -- canonical team name (after alias mapping)
  season         TEXT NOT NULL,      -- '2025-26' style — matches ncaab_team_stats
  rating_system  TEXT NOT NULL,      -- 'kenpom' | 'torvik' | 'massey' | 'haslam'
  ---
  -- Universal fields — every rating system produces these in some form
  adj_off        NUMERIC,             -- adjusted offensive efficiency (points per 100 possessions)
  adj_def        NUMERIC,             -- adjusted defensive efficiency
  adj_em         NUMERIC,             -- adjusted efficiency margin (adj_off - adj_def)
  tempo          NUMERIC,             -- possessions per 40 minutes
  ---
  -- Rank fields when available (some systems provide, some don't)
  em_rank        INT,
  ---
  -- Raw payload for anything not modeled above (four factors when available, etc.)
  raw_payload    JSONB,
  ---
  computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (snapshot_date, team, season, rating_system)
);

-- Panel model query: "latest snapshot per team per system for a season"
CREATE INDEX IF NOT EXISTS ncaab_rating_snapshots_recent_idx
  ON public.ncaab_rating_snapshots (season, team, rating_system, snapshot_date DESC);

-- Time-series analysis: "how has team X's KenPom rank moved over 2 weeks?"
CREATE INDEX IF NOT EXISTS ncaab_rating_snapshots_team_time_idx
  ON public.ncaab_rating_snapshots (team, snapshot_date DESC);

-- Daily rebuild: "what's today's latest snapshot for the Panel model?"
CREATE INDEX IF NOT EXISTS ncaab_rating_snapshots_by_date_idx
  ON public.ncaab_rating_snapshots (snapshot_date DESC);

-- RLS: readable by anon, writable by pipeline
ALTER TABLE public.ncaab_rating_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.ncaab_rating_snapshots;
CREATE POLICY public_read ON public.ncaab_rating_snapshots
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.ncaab_rating_snapshots;
CREATE POLICY public_write ON public.ncaab_rating_snapshots
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
