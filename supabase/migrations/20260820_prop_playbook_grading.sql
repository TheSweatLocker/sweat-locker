-- 2026-08-20: prop_playbook_decisions grading columns
--
-- Background: prop_playbook_decisions has been in shadow mode since 8/17,
-- accumulating 630 decisions. But the table has no grading column, so no
-- outcomes are ever attached — meaning shadow mode produces no useful
-- signal for the "promote out of shadow?" decision.
--
-- Fix: add result + graded_at + actual_value + grade_source so the new
-- grade_prop_playbook.py can grade decisions post-game.
--
-- Naming: mlb_pipeline_props.result stores 'Win'/'Loss'/'Push'/'Void'.
-- Per spec we shorten those to 'W'/'L'/'P'/'Void' on this table for
-- compactness. Grader handles the mapping.

BEGIN;

ALTER TABLE public.prop_playbook_decisions
  ADD COLUMN IF NOT EXISTS result       TEXT,          -- 'W' | 'L' | 'P' | 'Void' | NULL
  ADD COLUMN IF NOT EXISTS graded_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS actual_value NUMERIC,       -- actual stat produced (audit)
  ADD COLUMN IF NOT EXISTS grade_source TEXT;          -- 'mlb_pipeline_props' | 'stats_api' | 'manual'

-- Partial index: grader queries "ungraded rows for a given (sport, date)".
-- Partial index (WHERE result IS NULL) stays tiny once backfill completes.
CREATE INDEX IF NOT EXISTS idx_ppd_ungraded_sport_date
  ON public.prop_playbook_decisions (sport, game_date)
  WHERE result IS NULL;

COMMIT;

NOTIFY pgrst, 'reload schema';
