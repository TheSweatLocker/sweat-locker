-- 2026-09-10 · cohort_tag_records table
-- Per-sport per-tag historical record for the cohort_tags shown as chips on
-- game detail (NFL/NCAAF Situational + Cohort Signals expanders). Feeds the
-- "Home Favorite · 45-40-2 ATS (52.3%)" line under each chip so users see
-- what the tag actually means historically, not just its name.
--
-- Populated by mlb_pipeline/build_cohort_tag_records.py, which re-derives
-- each tag from nfl_game_results / ncaaf_game_results using the SAME rules
-- as nfl_game_context.compute_cohort_tags / ncaaf_game_context. Rebuilt daily
-- after new final scores land.
--
-- Feedback: project_cohort_signal_ux_909 (surface records on cohort chips),
--           project_badge_record_ledger_909 (per-badge historical ledger).

CREATE TABLE IF NOT EXISTS cohort_tag_records (
  id            BIGSERIAL PRIMARY KEY,
  sport         TEXT NOT NULL,           -- 'NFL' | 'NCAAF' (extend later)
  tag           TEXT NOT NULL,           -- e.g. 'nfl_home_fav', 'ncaaf_shootout'
  market        TEXT NOT NULL,           -- 'ats' (spread) | 'total' (over/under)
  side          TEXT NOT NULL DEFAULT 'primary', -- for future tag-side tracking ('over','under','home','away')
  wins          INT  NOT NULL DEFAULT 0,
  losses        INT  NOT NULL DEFAULT 0,
  pushes        INT  NOT NULL DEFAULT 0,
  hit_rate      NUMERIC(5,2),            -- percentage 0-100
  sample_n      INT  NOT NULL DEFAULT 0, -- wins + losses (excludes pushes)
  season_scope  TEXT NOT NULL DEFAULT 'lifetime', -- 'lifetime' | 'l1yr' | 'l2yr' etc.
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (sport, tag, market, side, season_scope)
);

CREATE INDEX IF NOT EXISTS cohort_tag_records_sport_tag_idx
  ON cohort_tag_records (sport, tag);

-- RLS: read-only for all (backend service writes via service_role)
ALTER TABLE cohort_tag_records ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'cohort_tag_records'
      AND policyname = 'cohort_tag_records_read_all'
  ) THEN
    CREATE POLICY cohort_tag_records_read_all ON cohort_tag_records
      FOR SELECT TO anon, authenticated USING (true);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
