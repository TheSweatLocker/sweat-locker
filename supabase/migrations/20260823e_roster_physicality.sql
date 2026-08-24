-- Roster physicality (2026-08-23) — sport-universal team-level physical
-- attribute cache. Feeds shadow signals for NCAAF/NCAAB (see companion
-- 20260823f_roster_physicality_signals.sql).
--
-- Design notes
-- ────────────
-- Sports where physicality is theoretically load-bearing on outcomes:
--   NCAAF: OL/DL weight advantage → ground-game leverage in cold/wet;
--          class-year experience → Weeks 1-3 edge (freshman-heavy teams
--          under-perform vs upperclass-heavy teams early season)
--   NCAAB: frontcourt height → O-reb rate + rim protection
--          class experience → early season execution
--
-- Sport-universal shape means NFL/NBA can drop in with zero schema
-- change if we later decide the signal has legs there. Keyed on (sport,
-- team, season) — one row per team per season.
--
-- Aggregations pre-computed at scrape time so signal evaluation stays
-- O(1) lookups. Position-group breakdowns stored as JSONB to preserve
-- flexibility (position taxonomies differ by sport).

CREATE TABLE IF NOT EXISTS public.roster_physicality (
  sport                     TEXT NOT NULL,
  team                      TEXT NOT NULL,           -- canonical_name (matches game_context)
  season                    INT  NOT NULL,

  -- Roster-wide aggregates
  n_players                 INT,
  avg_age                   NUMERIC,                 -- years (NULL for college — DOB private)
  avg_ht_in                 NUMERIC,                 -- inches
  avg_wt_lb                 NUMERIC,                 -- lbs
  avg_class_year            NUMERIC,                 -- 1=FR, 2=SO, 3=JR, 4=SR, 5=GR (college)
                                                     -- for pros: years_pro
  pct_upperclass            NUMERIC,                 -- share of JR+SR+GR (college only)

  -- Position group breakdowns (JSONB — schema differs per sport)
  --   NCAAF: {ol: {avg_wt, n, avg_class}, dl: {...}, qb: {...}, ...}
  --   NCAAB: {frontcourt: {avg_ht, n, avg_class}, backcourt: {...}, ...}
  --   NFL:   {ol: {...}, dl: {...}, ...}  (future)
  --   NBA:   {frontcourt: {...}, backcourt: {...}, ...}  (future)
  position_groups           JSONB,

  -- Source tracking
  source                    TEXT DEFAULT 'espn',     -- espn / cfbd / manual
  source_url                TEXT,
  updated_at                TIMESTAMPTZ DEFAULT NOW(),

  PRIMARY KEY (sport, team, season)
);

CREATE INDEX IF NOT EXISTS idx_roster_physicality_sport_season
  ON public.roster_physicality (sport, season);
CREATE INDEX IF NOT EXISTS idx_roster_physicality_team
  ON public.roster_physicality (team);

-- ─────────────────────────────────────────────────────────────
-- Context columns — NCAAF
-- ─────────────────────────────────────────────────────────────
-- Enricher writes these post-scrape so signals can read via ctx.
-- Every column additive & nullable → safe to backfill or leave NULL
-- for teams without roster data.

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_ol_avg_wt         NUMERIC,
  ADD COLUMN IF NOT EXISTS away_ol_avg_wt         NUMERIC,
  ADD COLUMN IF NOT EXISTS home_dl_avg_wt         NUMERIC,
  ADD COLUMN IF NOT EXISTS away_dl_avg_wt         NUMERIC,
  ADD COLUMN IF NOT EXISTS ol_dl_weight_gap_home  NUMERIC,  -- home OL avg - away DL avg (positive = home OL heavier)
  ADD COLUMN IF NOT EXISTS ol_dl_weight_gap_away  NUMERIC,  -- away OL avg - home DL avg
  ADD COLUMN IF NOT EXISTS home_avg_class_year    NUMERIC,
  ADD COLUMN IF NOT EXISTS away_avg_class_year    NUMERIC,
  ADD COLUMN IF NOT EXISTS class_year_edge_home   NUMERIC;  -- home - away (positive = home more experienced)

-- ─────────────────────────────────────────────────────────────
-- Context columns — NCAAB
-- ─────────────────────────────────────────────────────────────

ALTER TABLE public.ncaab_game_context
  ADD COLUMN IF NOT EXISTS home_frontcourt_avg_ht NUMERIC,
  ADD COLUMN IF NOT EXISTS away_frontcourt_avg_ht NUMERIC,
  ADD COLUMN IF NOT EXISTS frontcourt_ht_gap_home NUMERIC,  -- home FC - away FC (inches, positive = home taller frontcourt)
  ADD COLUMN IF NOT EXISTS home_avg_ht_in         NUMERIC,
  ADD COLUMN IF NOT EXISTS away_avg_ht_in         NUMERIC,
  ADD COLUMN IF NOT EXISTS home_avg_class_year    NUMERIC,
  ADD COLUMN IF NOT EXISTS away_avg_class_year    NUMERIC,
  ADD COLUMN IF NOT EXISTS class_year_edge_home   NUMERIC;

-- ─────────────────────────────────────────────────────────────
-- RLS — read all, write via service key
-- ─────────────────────────────────────────────────────────────
DO $$
BEGIN
  ALTER TABLE public.roster_physicality ENABLE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

DO $$
BEGIN
  DROP POLICY IF EXISTS "roster_phys select all" ON public.roster_physicality;
  CREATE POLICY "roster_phys select all" ON public.roster_physicality
    FOR SELECT USING (true);
  DROP POLICY IF EXISTS "roster_phys write service" ON public.roster_physicality;
  CREATE POLICY "roster_phys write service" ON public.roster_physicality
    FOR ALL USING (true) WITH CHECK (true);
END $$;

NOTIFY pgrst, 'reload schema';
