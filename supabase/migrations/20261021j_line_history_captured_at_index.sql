-- == 2026-10-21 - THE INDEX THE HEAVY QUERY ACTUALLY NEEDS ==
--
-- get_io_audit named the drain. One shape:
--
--     694.69 GiB read from disk · 129,297 calls · 202ms mean · 26,134s total
--
-- That is the Disk IO Budget, by itself. It is detect_line_movement's
-- detect_steam() read (mlb_pipeline/detect_line_movement.py:75):
--
--     sport = $1 AND captured_at >= $2
--     ORDER BY captured_at ASC
--     LIMIT 1000 OFFSET {0,1000,...,19000}
--
-- 20261021b ADDED THE WRONG INDEX. It created
-- (sport, market, side, commence_time DESC), because the advisor report
-- described the hot shape as "sport + commence_time lower bound". The actual
-- query filters and orders on **captured_at**, which is a different column
-- with a different meaning: commence_time is when the GAME starts,
-- captured_at is when the QUOTE was polled. No index covered
-- (sport, captured_at), so every one of those 129,297 calls fell back to
-- scanning. I took the advisor's wording for the caller's behaviour instead
-- of reading the caller -- the same mistake as trusting a comment over the
-- code that writes the column.
--
-- This index serves all three parts of that query at once: `sport` equality
-- narrows, `captured_at` serves both the range predicate and the ORDER BY,
-- so the sort disappears too. Equality column first, range column last --
-- the reverse cannot use the equality to narrow.
--
-- NOT A COMPLETE FIX ON ITS OWN. The same query paginates with deep OFFSET,
-- and OFFSET 19000 makes Postgres walk and discard 19,000 index entries
-- before returning a row, so cost grows quadratically with page count:
-- roughly 210,000 entry-reads to return 20,000 rows. market_line._page is
-- worse, capped at 120,000 rows = 120 pages ~ 7.3M entry-reads. Those
-- callers are being converted to keyset pagination separately; the index
-- makes each page cheap, keyset makes the number of pages stop multiplying.
--
-- CONCURRENTLY so this takes no write lock on a 3.6M-row table the pollers
-- are inserting into 235,000 times a day. That means it CANNOT run inside a
-- transaction block -- if the migration runner wraps statements in
-- BEGIN/COMMIT, run this file statement-by-statement in the SQL editor.

CREATE INDEX CONCURRENTLY IF NOT EXISTS line_history_sport_captured_idx
  ON public.line_history (sport, captured_at);

-- prune_line_history deletes by captured_at alone with no sport filter
-- (7.51 GiB, 792 calls). A leading-column index cannot serve that, and the
-- advisor's inventory lists captured_at indexes that overlap, so the pruner
-- is left as-is rather than adding a third variant on a hunch. Revisit with
-- get_io_audit's never_scanned_indexes once the stats window is long enough
-- to trust -- idx_scan = 0 only means "not scanned since the last reset".

NOTIFY pgrst, 'reload schema';
