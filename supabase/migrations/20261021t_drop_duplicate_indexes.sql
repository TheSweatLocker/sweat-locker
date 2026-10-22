-- == 2026-10-21 - FOUR INDEX PAIRS DOING THE SAME WORK TWICE ==
--
-- B119 residual. get_io_audit() reports 4 duplicate groups: same column
-- list, same operator classes, same uniqueness, same predicate. Together
-- 8.4 MB, which is not the point - the point is WRITE cost. Every INSERT
-- into these tables maintains both copies, and nba_player_game_logs is
-- ingested nightly per player per sport.
--
--   nba_player_game_logs  idx_nba_plyr_gl_date
--                         nba_player_game_logs_name_date_idx
--                         (player_name, game_date DESC)        7.6 MB
--   mlb_game_results      mlb_game_results_game_id_key
--                         mlb_game_results_game_id_unique
--                         UNIQUE (game_id)                      864 kB
--   mlb_game_context      mlb_game_context_game_id_key
--                         mlb_game_context_game_id_unique
--                         UNIQUE (game_id)                       80 kB
--   mlb_game_context      idx_mlb_game_context_game_date (DESC)
--                         mlb_game_context_game_date_idx (ASC)   32 kB
--
-- The last pair is only a duplicate because a btree is readable in BOTH
-- directions - a plain (game_date) index serves ORDER BY game_date DESC by
-- walking backwards. The audit's sameness test compares indkey + indclass +
-- indisunique + indpred and correctly ignores the direction.
--
-- ── WHY A DO BLOCK AND NOT FOUR DROP STATEMENTS ─────────────────────────
-- Two of these pairs are UNIQUE, and a unique index may be BACKING A
-- CONSTRAINT. `DROP INDEX` on a constraint's index fails outright, and
-- dropping the wrong member of the pair would silently remove the only
-- enforcement of a uniqueness rule the pipeline relies on for its upserts
-- (on_conflict=game_id). The `_key` suffix is what Postgres auto-names a
-- constraint index and `_unique` looks hand-made, but that is a guess about
-- a naming convention, not a fact about this database - and guessing which
-- index enforces a constraint is exactly how an on_conflict target
-- disappears.
--
-- So the catalog decides, per group, at run time:
--   * if exactly one member backs a constraint -> drop the OTHER
--   * if neither does -> drop the larger one (identical content, so keep
--     whichever is already tighter packed)
--   * if BOTH do -> drop NOTHING and say so. That needs ALTER TABLE DROP
--     CONSTRAINT, which changes what the table guarantees, and that is a
--     decision rather than a cleanup.
--
-- Plain DROP INDEX, no CONCURRENTLY: dropping is a catalog update with no
-- heap rewrite. lock_timeout so this waits five seconds for a conflicting
-- lock and gives up rather than queuing behind a long read and blocking
-- every writer behind itself.

BEGIN;

-- SET LOCAL needs a transaction to mean anything, hence the explicit
-- BEGIN: run as separate statements it would silently do nothing.
SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
  grp     RECORD;
  a       TEXT;
  b       TEXT;
  a_con   BOOLEAN;
  b_con   BOOLEAN;
  victim  TEXT;
  victim_oid OID;
  dropped INT := 0;
  skipped INT := 0;
BEGIN
  FOR grp IN
    SELECT i.indrelid::regclass::TEXT      AS tbl,
           array_agg(i.indexrelid::regclass::TEXT
                     ORDER BY pg_relation_size(i.indexrelid) DESC,
                              i.indexrelid::regclass::TEXT)  AS idxs,
           array_agg(i.indexrelid ORDER BY
                     pg_relation_size(i.indexrelid) DESC,
                     i.indexrelid::regclass::TEXT)           AS oids
      FROM pg_index i
      JOIN pg_class c  ON c.oid = i.indexrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND i.indisvalid
     GROUP BY i.indrelid,
              i.indkey::TEXT, i.indclass::TEXT,
              i.indisunique, COALESCE(pg_get_expr(i.indpred, i.indrelid), '')
    HAVING COUNT(*) > 1
  LOOP
    -- Only ever handle a clean PAIR. Three identical indexes is a different
    -- situation and should be looked at, not automated over.
    IF array_length(grp.idxs, 1) <> 2 THEN
      RAISE NOTICE '% : % identical indexes (%) - leaving alone, not a pair',
        grp.tbl, array_length(grp.idxs, 1), grp.idxs;
      skipped := skipped + 1;
      CONTINUE;
    END IF;

    a := grp.idxs[1];  -- larger
    b := grp.idxs[2];
    SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conindid = grp.oids[1])
      INTO a_con;
    SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conindid = grp.oids[2])
      INTO b_con;

    IF a_con AND b_con THEN
      RAISE NOTICE '% : BOTH % and % back constraints - dropping NOTHING. '
                   'Needs ALTER TABLE DROP CONSTRAINT, which is a decision.',
        grp.tbl, a, b;
      skipped := skipped + 1;
      CONTINUE;
    ELSIF a_con THEN
      victim := b; victim_oid := grp.oids[2];
    ELSIF b_con THEN
      victim := a; victim_oid := grp.oids[1];
    ELSE
      -- neither constrained: drop the larger copy
      victim := a; victim_oid := grp.oids[1];
    END IF;

    -- The OID, not a parsed name. regclass renders a schema qualification
    -- only when the schema is outside search_path, so splitting the text
    -- form on '.' is a guess about search_path; the oid is not.
    EXECUTE format('DROP INDEX %s', victim_oid::regclass);
    RAISE NOTICE '% : dropped % (kept the other copy of the same index)',
      grp.tbl, victim;
    dropped := dropped + 1;
  END LOOP;

  RAISE NOTICE 'duplicate index cleanup: % dropped, % left for a human',
    dropped, skipped;

  IF dropped = 0 AND skipped = 0 THEN
    RAISE NOTICE 'no duplicate index groups found - already clean';
  END IF;
END $$;

COMMIT;
