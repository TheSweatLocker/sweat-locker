-- == 2026-10-22 · THE QB CARD COULD NOT READ ITS OWN TABLE ==
--
-- ANDY, minutes after the QB card shipped: "Not seeing projection of nfl qbs
-- no projection - thin game log".
--
-- The projections were written and correct. The app could not see them.
--
--     service_role  GET nfl_engine_player_projections -> 200, 27 rows
--     anon          GET nfl_engine_player_projections -> 200, []
--     anon          GET nfl_starters (control)        -> 200, 1 row
--
-- A 200 WITH AN EMPTY ARRAY is the whole problem. PostgREST does not say
-- "forbidden" for a table the role cannot read — it returns success and
-- nothing. So the card's own fallback fired and rendered "no projection —
-- thin game log", which is a sentence about Drake Maye's game log and had
-- nothing to do with the actual cause. The card confidently explained a
-- problem it had misdiagnosed.
--
-- This is feedback_zero_rows_is_a_wiring_error exactly: zero rows read is a
-- WIRING ERROR until proven otherwise, never a finding about the data. I
-- verified the write with the service key, saw 27 rows, and shipped —
-- without once reading the table as the client reads it.
--
-- ── WHY IT HAPPENED ─────────────────────────────────────────────────────
-- 20261022c created the table and granted nothing. Every table the app
-- reads was set up with an explicit read grant (see
-- 20260913d_nfl_rosters_current, the pattern copied here); a new table
-- inherits none of it. Creating a table is therefore two jobs, and the
-- second one is silent when skipped.
--
-- ── READ ONLY, DELIBERATELY ─────────────────────────────────────────────
-- SELECT to anon/authenticated and nothing else. The bundled anon key ships
-- inside the IPA and is extractable by anyone who downloads the app, and on
-- 2026-10-21 that key was found able to UPDATE and DELETE public_receipts
-- (project_anon_key_can_write_1021). A projection table is written by the
-- pipeline and read by the app — there is no path that needs the app to
-- write here, so it does not get the ability.

ALTER TABLE public.nfl_engine_player_projections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS nfl_engine_player_projections_read
    ON public.nfl_engine_player_projections;
CREATE POLICY nfl_engine_player_projections_read
    ON public.nfl_engine_player_projections
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.nfl_engine_player_projections TO anon, authenticated;
GRANT ALL    ON public.nfl_engine_player_projections TO service_role;

--: GENERATED ALWAYS AS IDENTITY still creates a sequence, and the writer
--: needs it. Named after the table + column per Postgres convention.
GRANT USAGE, SELECT ON SEQUENCE nfl_engine_player_projections_id_seq
    TO service_role;

NOTIFY pgrst, 'reload schema';
