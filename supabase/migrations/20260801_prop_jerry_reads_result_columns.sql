-- 2026-08-01 · Add grading columns to prop_jerry_reads.
--
-- Original 20260731d migration created the table but forgot the
-- graded-outcome columns. grade_prop_jerry_reads.py was silently
-- failing to write results because the columns didn't exist —
-- meaning all Prop Jerry BACK/FADE calls from 7/31 launch day
-- lived ungraded. This migration closes the loop.

ALTER TABLE prop_jerry_reads
    ADD COLUMN IF NOT EXISTS result       text,
    ADD COLUMN IF NOT EXISTS resolved_at  timestamptz,
    ADD COLUMN IF NOT EXISTS actual_pa    jsonb;

CREATE INDEX IF NOT EXISTS idx_prop_jerry_reads_result
    ON prop_jerry_reads (game_date, result)
    WHERE result IS NULL;

NOTIFY pgrst, 'reload schema';
