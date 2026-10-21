-- ══ 2026-10-21 · DISK IO · INDEX THE SHAPES WE ACTUALLY QUERY ══
-- Supabase warned the project was about to deplete its Disk IO budget, and
-- pg_stat_statements named the cause. line_history (~3.36M rows) dominates:
--
--   sport + commence_time lower bound, no market/side
--       128,147 calls · 69.2M shared blocks ≈ 528 GiB cumulative · ~190ms mean
--   sport + market + side + commence_time, ORDER BY id
--           472 calls · ≈ 137 GiB cumulative · ~2.9s mean
--   sport + market + commence_time
--        16,390 calls · ≈ 76.5 GiB cumulative
--
-- The advisor's index inventory reports (sport, commence_time) and
-- (game_id, market, side) exist, but NOTHING matching the frequent
-- sport + market + side combination. The 2.9s mean on the second query is the
-- fingerprint of that gap: it filters on three columns and can use an index
-- on one.
--
-- WHY AN INDEX AND NOT A QUERY REWRITE FIRST. The callers are many and
-- scattered — fifteen pipeline scripts plus the client's Steam Room sparkline
-- fetch — so one index helps every one of them, today, without touching
-- behaviour. The query-side work (narrower selects, keyset pagination instead
-- of deep OFFSET) is worth doing and is logged, but it is a per-caller change
-- and this is not.
--
-- COLUMN ORDER IS DELIBERATE: equality columns first, range column last.
-- sport/market/side are all equality filters; commence_time is a range. An
-- index with the range column before the equality columns cannot use the
-- equalities to narrow, which is the mistake that leaves a composite index
-- looking useless in EXPLAIN.
--
-- CONCURRENTLY so this does not take a write lock on a 3.36M row table that
-- the pollers are inserting into. That means it CANNOT run inside a
-- transaction block — if the migration runner wraps statements in BEGIN/COMMIT
-- this file must be run statement-by-statement in the SQL editor instead.
--
-- VERIFY BEFORE TRUSTING, as the advisor says: run the real parameterised
-- query under EXPLAIN (ANALYZE, BUFFERS) and confirm shared-block reads drop.
-- An index that is not chosen costs write amplification and buys nothing.

CREATE INDEX CONCURRENTLY IF NOT EXISTS line_history_sport_market_side_ct_idx
  ON public.line_history (sport, market, side, commence_time DESC);

-- public_splits_v2 (~552k rows) is read filtered by sport + snapshot_ts and
-- the inventory lists no index combining them.
CREATE INDEX CONCURRENTLY IF NOT EXISTS public_splits_v2_sport_snapshot_idx
  ON public.public_splits_v2 (sport, snapshot_ts DESC);

-- ── NOT DONE HERE, ON PURPOSE ────────────────────────────────────────────
-- The advisor found TWO IDENTICAL (sport, commence_time DESC) indexes on
-- line_history, ~39 MB each, plus overlapping captured_at indexes. Dropping a
-- duplicate is real savings in storage and in write amplification on a table
-- taking 51,856 inserts — but a DROP is not reversible in the way an unused
-- CREATE is, and the advisor itself says to confirm no dependency first.
--
-- To action deliberately, after reading the names off pg_indexes:
--     DROP INDEX CONCURRENTLY IF EXISTS <the redundant one>;
--
-- Likewise retention: line_history is explicitly kept for pattern mining with
-- no purge. 3.36M rows is the thing being scanned. A retention policy is a
-- product decision about how far back pattern mining needs to see, and must
-- not be guessed at here.

NOTIFY pgrst, 'reload schema';
