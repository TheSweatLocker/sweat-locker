-- 2026-09-19  Second compat shim -- same class as 20260919d.
--
-- Found by mlb_pipeline/verify_app_select_columns.py AFTER the audit was
-- widened from a hardcoded 2-file list to every app file that talks to
-- PostgREST. The narrow version checked 99 of the app's 143 .from()
-- calls and missed all 25 in GameDetailV2.tsx -- the component that
-- renders the very screens the check exists to protect.
--
-- app/components/GameDetailV2.tsx asked nfl_player_projections for
-- `proj_pass_yards`. The column is `proj_pass_yds`. One wrong name 400s
-- the whole select, so the NFL QB Matchup card lost ALL its projections
-- (fantasy points and pass TDs too, not just pass yards).
--
-- The app is fixed going forward, but the released build still sends the
-- old name, so we serve it: a generated mirror of the real column.
-- Generated is safe here -- the pipeline writer
-- (nfl_espn_projections_pull.py / nfl_sleeper_projections_pull.py) emits
-- proj_pass_yds and has no concept of proj_pass_yards, so it can never
-- supply this column and trigger 428C9.
--
-- RETENTION: drop once traffic from builds <= 2026-09-19 is negligible.

ALTER TABLE public.nfl_player_projections
  ADD COLUMN IF NOT EXISTS proj_pass_yards numeric
    GENERATED ALWAYS AS (proj_pass_yds) STORED;

COMMENT ON COLUMN public.nfl_player_projections.proj_pass_yards IS
  'Compat shim for builds <= 2026-09-19 (see 20260919e). Mirror of '
  'proj_pass_yds, which is the real column -- write to that one.';

NOTIFY pgrst, 'reload schema';
