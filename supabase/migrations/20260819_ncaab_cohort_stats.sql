-- NCAAB cohort stats — dedicated NCAAB cohort table (2026-08-19).
--
-- Complement to mlb_tier_calibration (which the existing
-- ncaab_cohort_backfill.py already writes to, cross-sport). This
-- NCAAB-scoped table exists so we can carry richer metadata per
-- cohort (category, description, notes) that the shared table doesn't
-- have room for.
--
-- Grading: straight-up only for offseason cohorts (no odds available
-- on the 5,911 backfilled 2024-25 games). ATS cohorts get added once
-- ncaab_odds_pull.py starts flowing lines Nov 2026.
--
-- Populated by ncaab_cohort_backfill.py (extended for offseason cohorts:
-- rest advantage, season phase, ranked-vs-unranked, home/road splits).

CREATE TABLE IF NOT EXISTS public.ncaab_cohort_stats (
  cohort_key      TEXT        NOT NULL,
  window_label    TEXT        NOT NULL,           -- 'lifetime_su_2024_25', etc.
  computed_date   DATE        NOT NULL DEFAULT CURRENT_DATE,

  category        TEXT,                            -- 'home_court' | 'rest' | 'season_phase' | 'ranked' | 'kenpom' | 'shooting' | 'pace' | 'blowout'
  description     TEXT,                            -- human-readable cohort definition

  sport           TEXT        NOT NULL DEFAULT 'NCAAB',
  hits            INT         NOT NULL DEFAULT 0,
  losses          INT         NOT NULL DEFAULT 0,
  pushes          INT         NOT NULL DEFAULT 0,
  total           INT         NOT NULL DEFAULT 0,
  hit_rate        NUMERIC(5,4),                    -- hits / (hits + losses)

  -- Optional metadata (for info-only cohorts)
  info_metric     TEXT,                            -- e.g. 'avg_points'
  info_value      NUMERIC(6,2),                    -- e.g. 148.3

  notes           TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  PRIMARY KEY (cohort_key, window_label, computed_date)
);

CREATE INDEX IF NOT EXISTS ncaab_cohort_stats_cat_idx
  ON public.ncaab_cohort_stats (category);
CREATE INDEX IF NOT EXISTS ncaab_cohort_stats_hitrate_idx
  ON public.ncaab_cohort_stats (window_label, hit_rate DESC NULLS LAST);

COMMENT ON TABLE public.ncaab_cohort_stats IS
  'NCAAB-scoped cohort priors (SU or ATS depending on window_label). '
  'Sibling of mlb_tier_calibration but with richer metadata + NCAAB-only '
  'so we can iterate on cohort taxonomy without touching the shared table.';

NOTIFY pgrst, 'reload schema';
