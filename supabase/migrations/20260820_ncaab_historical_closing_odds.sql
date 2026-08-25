-- NCAAB historical closing odds (2026-08-20).
--
-- Landing table for backfill scripts that scrape closing spread / total /
-- ML from external sources for previously-played NCAAB games. This is a
-- provenance-tagged store separate from `ncaab_game_results` because:
--   * `ncaab_game_results` is written live by ncaab_odds_pull.py and its
--     closing-line columns reflect what was in the Odds API at pick time.
--     Retroactively rewriting those rows would corrupt the live audit
--     trail.
--   * Backfilled odds may come from different sources with different
--     quality tiers (efficiency-data-source game logs, The Odds API
--     historical, Kaggle datasets). We want to attribute + trust-tier
--     each row.
--   * The signal-validation backfills (ncaab_cohort_backfill.py, the
--     45-signal registry validator) LEFT JOIN this table onto
--     ncaab_game_results by (game_date, away_team, home_team) so an
--     enriched close_spread/close_total is available without touching
--     production writes.
--
-- Sign conventions match `ncaab_game_results` / `ncaab_game_context.py`:
--   close_spread: HOME perspective — NEGATIVE = home favored.
--   close_total : combined points.
--   close_*_ml  : American odds (e.g. -140, +115).
--
-- User-copy rule: per feedback_no_kenpom_attribution.md this table is
-- internal-only. `source` may name the specific vendor, but nothing
-- user-facing (Jerry writeups, external picks tab, card copy) may
-- reference a scraped source system by name.

CREATE TABLE IF NOT EXISTS public.ncaab_historical_closing_odds (
  id                BIGSERIAL   PRIMARY KEY,
  game_date         DATE        NOT NULL,
  away_team         TEXT        NOT NULL,           -- canonical_name via ncaab_team_aliases
  home_team         TEXT        NOT NULL,           -- canonical_name
  kenpom_game_id    INT,                            -- optional, from team.php href when parseable
  season            TEXT,                           -- '2024-25' etc — inferred from game_date

  -- Closing lines (all optional — populated whenever the source has them)
  close_home_ml     INT,                            -- American odds
  close_away_ml     INT,
  close_spread      NUMERIC(5,2),                   -- home perspective; NEGATIVE = home favored
  close_total       NUMERIC(5,2),

  -- Provenance
  source            TEXT        NOT NULL DEFAULT 'kenpom_team_php',
  source_url        TEXT,                           -- specific page scraped (audit)
  raw_payload       JSONB,                          -- parser dump for debugging (line as string, etc.)
  fetched_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (game_date, away_team, home_team)
);

CREATE INDEX IF NOT EXISTS ncaab_hist_odds_date_idx
  ON public.ncaab_historical_closing_odds (game_date);
CREATE INDEX IF NOT EXISTS ncaab_hist_odds_season_idx
  ON public.ncaab_historical_closing_odds (season);
CREATE INDEX IF NOT EXISTS ncaab_hist_odds_source_idx
  ON public.ncaab_historical_closing_odds (source);

COMMENT ON TABLE public.ncaab_historical_closing_odds IS
  'Backfilled closing odds for previously-played NCAAB games. LEFT JOIN '
  'onto ncaab_game_results by (game_date, away_team, home_team) when '
  'validating signals that need close_spread/close_total. Written by '
  'offseason backfill scripts, not by the live odds puller. '
  'INTERNAL ONLY — user-facing copy must never name a source system.';

NOTIFY pgrst, 'reload schema';
