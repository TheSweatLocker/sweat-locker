-- NCAAB Panel prediction column (2026-08-14).
--
-- Session 3 · lens #2 of the 5-lens stack (MC + Panel + V4 + KenPom + Jerry).
--
-- Panel aggregates independent rating systems (KenPom + Torvik + Haslam) into
-- a single wisdom-of-crowds projection. Written by ncaab_panel_predictor.py.
-- Reads:
--   ncaab_rating_snapshots (latest snapshot_date per team, all sources)
--   ncaab_game_context (game rows + pace/tempo from KenPom join)
--
-- Same design pattern as mc_probabilities: single JSONB blob, sport-universal
-- shape so downstream lens-count / anti-consensus logic reads one field name.
--
-- Shape:
--   {
--     "panel_projected_margin": 4.8,      // home - away (HFA applied unless neutral)
--     "panel_projected_total": 145.2,
--     "panel_home_em": 22.5,              // mean of available systems
--     "panel_away_em": 15.2,
--     "panel_em_gap": 7.3,
--     "panel_home_off": 118.2,            // mean adj_off across systems
--     "panel_away_off": 112.5,
--     "panel_home_def": 95.7,
--     "panel_away_def": 97.3,
--     "panel_systems_home": 3,            // 2 or 3 required to fire
--     "panel_systems_away": 3,
--     "panel_em_stddev_home": 1.2,        // system agreement
--     "panel_em_stddev_away": 2.8,
--     "panel_confidence": "high",         // low/medium/high per max stddev
--     "panel_vs_kenpom_margin_delta": 0.8,// sanity check: panel vs single-source
--     "panel_neutral_site": false,
--     "generated_at": "2026-08-14T..."
--   }
--
-- panel_prediction NULL means at least one team had <2 rating systems available
-- (Torvik/Haslam name-alias gap — S1 followup work).

ALTER TABLE public.ncaab_game_context
  ADD COLUMN IF NOT EXISTS panel_prediction JSONB;

CREATE INDEX IF NOT EXISTS ncaab_game_context_panel_present_idx
  ON public.ncaab_game_context (game_date DESC)
  WHERE panel_prediction IS NOT NULL;

NOTIFY pgrst, 'reload schema';
