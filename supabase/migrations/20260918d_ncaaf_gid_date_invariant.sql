-- 2026-09-18d — NCAAF game_id must encode its own game_date.
-- ==================================================================
-- ROOT-CAUSE GUARD for the UTC-vs-ET date drift that produced 52
-- orphan ctx rows and 13 active duplicate matchups.
--
-- What happened: ncaaf_odds_pull.py built game_id from the UTC date
-- while game_date came from the ET date. Thu/Fri night kickoffs (8pm+
-- ET) cross UTC midnight, so game_id landed one day ahead. A later
-- pull with a slightly different commence_time wrote a SECOND row
-- under the correct id — same game, two rows, conflicting spread and
-- tier. The Sharp Card composer sorts by conviction, so on 2026-09-18
-- it published Oregon -56.5 STRONG/77 off a 9/15 row while the live
-- row said -58.5 LEAN/62.
--
-- The write path was patched 2026-09-16 (ncaaf_odds_pull.py:163-174).
-- That fixed ONE writer. It did not stop the next writer from making
-- the same mistake, and it did not clean up existing rows. These two
-- constraints close both gaps at the DB level, so the invariant holds
-- no matter which code path does the insert.
--
-- CONSTRAINT 1 (the real guard): game_id's embedded date segment must
-- equal game_date. Any writer using a UTC date gets rejected outright
-- instead of silently creating a phantom row.
--
-- CONSTRAINT 2: one ctx row per (game_date, home_team, away_team).
-- Catches same-date duplicates from name-spelling drift, which is the
-- separate failure mode logged 2026-09-02.
--
-- ⚠ ORDER OF OPERATIONS — run the repair FIRST:
--     python mlb_pipeline/repair_ncaaf_gid_date_drift.py --dry-run
--     python mlb_pipeline/repair_ncaaf_gid_date_drift.py --apply
--   Both ALTERs below will FAIL on existing violating rows. That is
--   intentional: a failed migration means the cleanup is incomplete,
--   which is better than silently adding a NOT VALID constraint that
--   lets the bad rows keep living.
--
-- KNOWN RESIDUAL (needs a human call, does not block this migration):
--   Houston @ Texas Tech has one row on game_date 2026-09-19 with an
--   outlier -14.0 spread, while two independent pulls put the same
--   matchup on 2026-09-18 at -7.5. Both surviving rows are internally
--   consistent so they PASS these constraints. Same matchup on two
--   adjacent dates is a schedule-truth question, not an invariant
--   violation — resolve by confirming the real kickoff date, then
--   deleting the phantom row.
--
-- ROLLBACK:
--   ALTER TABLE public.ncaaf_game_context
--     DROP CONSTRAINT IF EXISTS ncaaf_ctx_gid_encodes_game_date;
--   ALTER TABLE public.ncaaf_game_context
--     DROP CONSTRAINT IF EXISTS ncaaf_ctx_one_row_per_matchup;
-- ==================================================================

-- ─── 1. game_id must encode game_date ──────────────────────────────
-- Expected shape: ncaaf_<YYYYMMDD>_<away>_<home>
ALTER TABLE public.ncaaf_game_context
    ADD CONSTRAINT ncaaf_ctx_gid_encodes_game_date
    CHECK (
        game_id IS NULL
        OR game_date IS NULL
        OR game_id LIKE 'ncaaf\_' || to_char(game_date, 'YYYYMMDD') || '\_%'
    );

-- ─── 2. one context row per matchup per date ───────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS ncaaf_ctx_one_row_per_matchup
    ON public.ncaaf_game_context (game_date, home_team, away_team);

NOTIFY pgrst, 'reload schema';
