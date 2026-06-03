-- 2026-06-03 — Props book-line preservation across cron passes.
--
-- Problem (recurring): every cron pass wipes + regenerates today's props.
-- attach_book_lines calls the Odds API per market; when the API is throttled,
-- a market hasn't posted yet, or a region scope is wrong (us2 missing 6/3),
-- the call returns 0 books → all props for that market land book_line=NULL
-- → tiered SKIP. Previously-attached book lines disappear.
--
-- Fix: stamp last_attached_at whenever a book line is written. Before the
-- wipe, snapshot {book_line, book_*_odds, book_source, last_attached_at}
-- per (game_id, player_name, prop_type). After the fresh attach, for any
-- prop that came back with NULL book_line but had a snapshot within 6
-- hours, restore the previously-attached line. Books move but not by 3+
-- innings of game-line — 6h is a safe staleness window for pre-game.
--
-- Trigger incident: 6/3 Williams BB U 1.5 went PRIME→SKIP→PRIME→SKIP twice
-- across the day because each cron pass got different Odds API results
-- (regions=us only, the us2-fix landed mid-day). With last_attached_at,
-- the second cron would have preserved morning's line.

ALTER TABLE mlb_pipeline_props
    ADD COLUMN IF NOT EXISTS last_attached_at TIMESTAMPTZ;

COMMENT ON COLUMN mlb_pipeline_props.last_attached_at IS
  'When the book_line + book_*_odds were last successfully attached from Odds API. Used by attach_book_lines to preserve lines across cron passes when a fresh API call returns no result (transient throttle, market not posted, region scope bug, etc.). Within a 6-hour window, NULL fresh attach falls back to the snapshot.';

NOTIFY pgrst, 'reload schema';
