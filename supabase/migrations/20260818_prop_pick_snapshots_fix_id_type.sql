-- 2026-08-18 hotfix: prop_pick_snapshots.prop_id must be BIGINT, not UUID.
-- Original migration used UUID because I mis-remembered the schema of
-- mlb_pipeline_props.id, which is actually BIGSERIAL/BIGINT. Every insert
-- was 400ing with "invalid input syntax for type uuid: \"41340\"".
--
-- Safe because the table is still empty (no rows to migrate). Drop the
-- unique index that references the column, alter the type, recreate.

BEGIN;

DROP INDEX IF EXISTS prop_pick_snapshots_uniq;

ALTER TABLE prop_pick_snapshots
  ALTER COLUMN prop_id TYPE BIGINT USING NULL;

CREATE UNIQUE INDEX IF NOT EXISTS prop_pick_snapshots_uniq
  ON prop_pick_snapshots (prop_id, snapshot_source, game_date);

COMMIT;

NOTIFY pgrst, 'reload schema';
