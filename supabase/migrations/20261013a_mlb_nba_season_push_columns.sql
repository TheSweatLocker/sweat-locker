-- 2026-10-13 · The four columns that killed every MLB and NBA trend patch
--
-- enrich_team_trends writes 17 season-trend fields. mlb_game_context and
-- nba_game_context were missing *_season_ats_pushes and *_season_ou_pushes
-- (NFL and NCAAF have all four), and PostgREST rejects a PATCH naming ANY
-- absent column as a whole — so all 17 fields were dropped on every run,
-- in both sports, silently. Measured 2026-10-12: home_season_cover_pct and
-- home_season_over_pct sat at 0/39 on MLB while 12 enabled MLB signals read
-- them, and NBA matched 0 of 31 team names on top of that.
--
-- enrich_team_trends now strip-retries, so the other 13 fields land even
-- without this migration. These four columns are added so the pushes stop
-- being thrown away, and so the warning it prints goes quiet.
--
-- Pushes are counts, not percentages: integer, nullable, no default. A NULL
-- means "not yet enriched"; a 0 means "enriched, no pushes". Those are
-- different facts and a DEFAULT 0 would erase the distinction.

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS home_season_ats_pushes integer,
  ADD COLUMN IF NOT EXISTS away_season_ats_pushes integer,
  ADD COLUMN IF NOT EXISTS home_season_ou_pushes  integer,
  ADD COLUMN IF NOT EXISTS away_season_ou_pushes  integer;

ALTER TABLE nba_game_context
  ADD COLUMN IF NOT EXISTS home_season_ats_pushes integer,
  ADD COLUMN IF NOT EXISTS away_season_ats_pushes integer,
  ADD COLUMN IF NOT EXISTS home_season_ou_pushes  integer,
  ADD COLUMN IF NOT EXISTS away_season_ou_pushes  integer;

COMMENT ON COLUMN mlb_game_context.home_season_ats_pushes IS
  'Season ATS pushes for the home team, from team_season_trends. Written by enrich_team_trends.py. NULL = not enriched; 0 = enriched with no pushes.';
COMMENT ON COLUMN nba_game_context.home_season_ats_pushes IS
  'Season ATS pushes for the home team, from team_season_trends. Written by enrich_team_trends.py. NULL = not enriched; 0 = enriched with no pushes.';

-- PostgREST caches the schema; without this the new columns 400 on select
-- and the writer keeps stripping them.
NOTIFY pgrst, 'reload schema';
