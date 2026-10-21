-- == 2026-10-21 - DROP 160 MB OF DUPLICATE INDEXES ON line_history ==
--
-- Confirmed from the catalog, not from a report. 20261021h's duplicate check
-- was broken (it grouped on pg_get_indexdef(), which embeds the index NAME,
-- so identical-column indexes could never match and it reported zero).
-- 20261021i fixed the grouping to column list + operator classes +
-- uniqueness + predicate, and the pairs are real:
--
--   idx_line_history_captured_at      btree (captured_at DESC)
--   line_history_captured_at_idx      btree (captured_at)           80 MB
--
--   line_history_sport_commence_idx   btree (sport, commence_time DESC)
--   line_history_sport_upcoming_idx   btree (sport, commence_time DESC)  79 MB
--
-- The second pair is byte-identical. The first differs only in declared sort
-- direction, and a btree can be scanned backwards, so a single-column ASC
-- index serves ORDER BY ... DESC equally well. One of each pair is pure
-- overhead.
--
-- WHY THIS IS AN IO FIX AND NOT JUST A STORAGE ONE. line_history takes about
-- 235,000 inserts a day (2.45M lifetime) and EVERY index on the table is
-- updated on EVERY insert. A redundant index is write amplification charged
-- per row, forever, against the same Disk IO Budget the reads are draining.
-- 160 MB of it on the busiest table in the database.
--
-- WHICH ONE SURVIVES: the conventionally-named one in each pair. Neither
-- appears in get_io_audit's never-scanned list, so both are being chosen by
-- the planner at times, which is exactly what redundancy looks like -- the
-- planner picks one arbitrarily and the other still pays the write cost.
-- Keeping either is equivalent.
--
-- idx_line_history_game_date is also dropped: 1080 kB, idx_scan = 0, and
-- pg_stat_database.stats_reset is NULL on this database, meaning the
-- counters have NEVER been cleared. So zero scans is an all-time zero rather
-- than an artefact of a recent reset -- the one condition that makes
-- "unused" safe to act on. The other 225 never-scanned indexes are left
-- alone for now; they total 84 MB across the schema and each needs the same
-- all-time check plus a look at whether a nightly job is its only caller.
--
-- NOT TOUCHED, deliberately: the duplicate UNIQUE indexes on
-- mlb_game_results(game_id) and mlb_game_context(game_id). One of each pair
-- almost certainly backs a UNIQUE CONSTRAINT, and DROP INDEX refuses with
-- "cannot drop index ... because constraint ... requires it", which would
-- abort this migration. They are 864 kB and 80 kB -- not worth the risk
-- here, and they need the constraint dropped rather than the index.
--
-- CONCURRENTLY so no write lock is taken on a table the pollers are
-- inserting into continuously. That means these CANNOT run inside a
-- transaction block: if the migration runner wraps statements in
-- BEGIN/COMMIT, run this file statement-by-statement in the SQL editor.

DROP INDEX CONCURRENTLY IF EXISTS public.idx_line_history_captured_at;
DROP INDEX CONCURRENTLY IF EXISTS public.line_history_sport_upcoming_idx;
DROP INDEX CONCURRENTLY IF EXISTS public.idx_line_history_game_date;

NOTIFY pgrst, 'reload schema';
