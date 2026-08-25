-- 2026-08-19: prop_playbook_decisions dedup + unique constraint
-- Bug: prop_ensemble_scorer.py does INSERT-only because table had no unique
-- constraint (comment on lines 44-46). Every rescore doubles the row count
-- for that (player, prop_type, direction, prop_line) on that date. 8/18
-- had 248 rows for 124 unique props post-rescore.
--
-- Fix: dedup existing rows (keep latest scored_at per key) + add unique
-- constraint + script switches to ON CONFLICT DO UPDATE.

BEGIN;

-- 1. Dedup: keep the row with the most-recent scored_at per key.
--    Ties broken by id (higher = newer).
DELETE FROM prop_playbook_decisions
WHERE id IN (
  SELECT id FROM (
    SELECT id,
      ROW_NUMBER() OVER (
        PARTITION BY sport, game_date, player_name, prop_type, direction, prop_line
        ORDER BY scored_at DESC NULLS LAST, id DESC
      ) AS rn
    FROM prop_playbook_decisions
  ) t
  WHERE rn > 1
);

-- 2. Unique constraint on the natural key. The scorer keys off
--    (sport, game_date, player, prop_type, direction, prop_line) —
--    that's the shape of one prop_row.
ALTER TABLE prop_playbook_decisions
  ADD CONSTRAINT prop_playbook_decisions_natural_key
  UNIQUE (sport, game_date, player_name, prop_type, direction, prop_line);

COMMIT;

NOTIFY pgrst, 'reload schema';
