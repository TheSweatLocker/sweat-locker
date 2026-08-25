-- NBA player projections + Sleeper signals (2026-08-17).
--
-- Adds nba_player_projections (Sleeper-driven season per-game averages)
-- + prop-row projection columns + signal seeds.
--
-- Sleeper API: /v1/projections/nba/regular/{season}
-- Provides pts / ast / reb / blk / stl / fgm / etc. per player, updated
-- as roles evolve. 690 players with meaningful projections.
--
-- Signal shape: when projected value diverges from book_line by ≥ threshold,
-- fire BACK/FADE. Mirrors the projection_edge pattern used for MLB.

CREATE TABLE IF NOT EXISTS public.nba_player_projections (
  id              BIGSERIAL PRIMARY KEY,
  sleeper_id      TEXT,
  player_name     TEXT NOT NULL,
  team_abbrev     TEXT,
  position        TEXT,
  season          TEXT NOT NULL,       -- '2024', '2025'
  -- Season per-game projections (Sleeper output)
  proj_pts        NUMERIC,
  proj_reb        NUMERIC,
  proj_ast        NUMERIC,
  proj_stl        NUMERIC,
  proj_blk        NUMERIC,
  proj_threes     NUMERIC,             -- 3-pointers made (derived from fgm/fga split)
  proj_fga        NUMERIC,
  proj_fgm        NUMERIC,
  proj_fta        NUMERIC,
  proj_ftm        NUMERIC,
  proj_turnovers  NUMERIC,
  proj_minutes    NUMERIC,
  proj_pra        NUMERIC,             -- points+reb+ast combo
  proj_pr         NUMERIC,             -- points+reb
  proj_pa         NUMERIC,             -- points+ast
  proj_ra         NUMERIC,             -- reb+ast
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (player_name, season)
);
CREATE INDEX IF NOT EXISTS idx_nba_proj_player ON public.nba_player_projections (player_name, season);

-- Add projected + edge columns to nba_pipeline_props so signals read them
ALTER TABLE public.nba_pipeline_props
  ADD COLUMN IF NOT EXISTS projected_value      NUMERIC,     -- Sleeper projected value for this stat
  ADD COLUMN IF NOT EXISTS proj_vs_line_edge    NUMERIC,     -- projected - book_line (positive = OVER edge)
  ADD COLUMN IF NOT EXISTS projections_updated_at TIMESTAMPTZ;

-- Seed 3 projection signals
DELETE FROM public.signal_sources
 WHERE sport = 'NBA' AND class = 'prop_model'
   AND origin = 'SEEDED_NBA_PROJ_817';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  ('nba_projection_edge_supports', 'NBA', 'prop_model', '*', 'prop',
   'p.get("proj_vs_line_edge") is not None and abs(float(p["proj_vs_line_edge"])) >= 1.0 and ((float(p["proj_vs_line_edge"]) > 0 and p.get("direction")=="over") or (float(p["proj_vs_line_edge"]) < 0 and p.get("direction")=="under"))',
   '"BACK"',
   'min(abs(float(p["proj_vs_line_edge"])) / 3.0, 1.0)',
   'Sleeper projects {projected_value} vs line {prop_line} — {proj_vs_line_edge} {direction} edge',
   'Sleeper season projection meaningfully supports direction (edge >= 1.0)',
   true, 'SEEDED_NBA_PROJ_817'),

  ('nba_projection_edge_strong', 'NBA', 'prop_model', '*', 'prop',
   'p.get("proj_vs_line_edge") is not None and abs(float(p["proj_vs_line_edge"])) >= 3.0 and ((float(p["proj_vs_line_edge"]) > 0 and p.get("direction")=="over") or (float(p["proj_vs_line_edge"]) < 0 and p.get("direction")=="under"))',
   '"BACK"',
   'min(abs(float(p["proj_vs_line_edge"])) / 5.0, 1.0)',
   'Sleeper STRONG projection edge {proj_vs_line_edge} supports {direction}',
   'Sleeper projection 3+ off book line in pick direction — high-confidence',
   true, 'SEEDED_NBA_PROJ_817'),

  ('nba_projection_edge_opposes', 'NBA', 'prop_model', '*', 'prop',
   'p.get("proj_vs_line_edge") is not None and abs(float(p["proj_vs_line_edge"])) >= 2.0 and ((float(p["proj_vs_line_edge"]) < 0 and p.get("direction")=="over") or (float(p["proj_vs_line_edge"]) > 0 and p.get("direction")=="under"))',
   '"FADE"',
   'min(abs(float(p["proj_vs_line_edge"])) / 4.0, 1.0)',
   'Sleeper projection OPPOSES {direction} — {proj_vs_line_edge} gap',
   'Sleeper projection lands on OTHER side of book line by 2+ — fade signal',
   true, 'SEEDED_NBA_PROJ_817');

-- RLS
ALTER TABLE public.nba_player_projections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.nba_player_projections;
CREATE POLICY public_read ON public.nba_player_projections FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS service_role_write ON public.nba_player_projections;
CREATE POLICY service_role_write ON public.nba_player_projections FOR ALL TO service_role USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
