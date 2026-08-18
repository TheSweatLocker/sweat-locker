-- 2026-08-18: extend team_form data with venue-split L10 + H2H columns.
--
-- User's ask: "look at last X matchups against the opponent... Last 10 ATS,
-- Last 10 at home ATS, Last 10 at home ML (same for away). A quant would
-- look at these. For NCAAB process I look at these."
--
-- Existing L10 columns aggregate ALL games. What's missing:
--   1. Venue split — home team's L10 AT HOME vs on the road (& same for away)
--      Coors, Yankee Stadium, Fenway all bias results — split matters.
--   2. H2H — how these two specific teams have performed vs each other lately
--      NCAAB pattern the user cited: rivalries + coach matchups persist.
--
-- Universal migration: adds the same columns to every sport's game_context.
-- Universal enricher (enrich_team_form_universal.py, next commit) populates.
-- New signal_sources rows evaluate them.

BEGIN;

DO $$
DECLARE
  t text;
  ctx_tables text[] := ARRAY[
    'mlb_game_context',
    'nfl_game_context',
    'ncaaf_game_context',
    'ncaab_game_context',
    'nhl_game_context',
    'nba_game_context'
  ];
BEGIN
  FOREACH t IN ARRAY ctx_tables LOOP
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema='public' AND table_name=t) THEN
      -- Venue-split L10 records
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS home_ats_l10_at_home INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS home_ats_l10_at_home_losses INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS home_ml_l10_at_home INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS home_ml_l10_at_home_losses INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS home_ou_l10_at_home_overs INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS home_ou_l10_at_home_unders INT', t);

      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS away_ats_l10_on_road INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS away_ats_l10_on_road_losses INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS away_ml_l10_on_road INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS away_ml_l10_on_road_losses INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS away_ou_l10_on_road_overs INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS away_ou_l10_on_road_unders INT', t);

      -- H2H last-5 records (this specific matchup, regardless of home/away)
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS h2h_last5_home_wins INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS h2h_last5_home_covers INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS h2h_last5_overs INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS h2h_last5_games_played INT', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS h2h_last5_avg_total NUMERIC', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS h2h_last5_avg_margin NUMERIC', t);
      EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS team_form_enriched_at TIMESTAMPTZ', t);
    END IF;
  END LOOP;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
