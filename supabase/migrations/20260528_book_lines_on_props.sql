-- Book line reference columns on mlb_pipeline_props.
--
-- Trigger: 5/28 audit revealed every prop line displayed in the app is OUR
-- internally computed `suggested_line`, not a real sportsbook line. Sale's
-- "Over 5.1 (proj 8.0)" trap and Flaherty's "Under 14.5 (book actual 16.5)"
-- both stemmed from this — users couldn't bet our displayed lines because
-- no book posts them. Real book lines are reference data only; the pipeline
-- conviction scoring + projections remain the source of truth for which
-- props surface and at what tier.
--
-- Populated by fetch_pitcher_prop_lines (called inside generate_props.py).
-- NULL when book line unavailable (e.g. market not posted for that pitcher).

ALTER TABLE mlb_pipeline_props
    ADD COLUMN IF NOT EXISTS book_line       NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS book_over_odds  INTEGER,
    ADD COLUMN IF NOT EXISTS book_under_odds INTEGER,
    ADD COLUMN IF NOT EXISTS book_source     TEXT;

NOTIFY pgrst, 'reload schema';
