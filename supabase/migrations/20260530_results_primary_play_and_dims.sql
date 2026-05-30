-- 2026-05-30 — Snapshot the headline play decision into mlb_game_results.
--
-- Why: the backfill audit for the 5/30 sweat redesign couldn't grade SIDE
-- PRIME calls because primary_play wasn't preserved historically. Going
-- forward, log primary_play + the full sweat_dimensions struct on each
-- result row so the audit can match the actual headline call against the
-- actual outcome (ML winner, spread cover, total result, NRFI).
--
-- Both columns are JSONB so the existing dict structures pass through
-- unchanged. NULL when log_game_result fires before play_of_day.py has
-- written sweat_dimensions back to mlb_game_context (i.e. resolved games
-- that never had a sweat run — rare but possible).

ALTER TABLE mlb_game_results
    ADD COLUMN IF NOT EXISTS primary_play JSONB,
    ADD COLUMN IF NOT EXISTS sweat_dimensions JSONB;

COMMENT ON COLUMN mlb_game_results.primary_play IS
  'The headline play designated for this game (ML / spread / total /
   YRFI / NRFI). Snapshot from mlb_game_context.primary_play at result-
   log time. Used by audit scripts to grade headline calls against
   outcomes. JSONB struct: {type, tier, label, sub, signal_floor,
   audit_note?}. NULL when no qualifying play was found pre-game.';

COMMENT ON COLUMN mlb_game_results.sweat_dimensions IS
  '5/29 dimensional sweat-score split (side / total / prop). JSONB struct:
   {side, total, prop, winning_dimension, model_play, supplementary_play}.
   Each dimension carries score / tier / drivers list / play. Used by
   audit scripts to per-dimension hit rates over time. NULL when result
   was logged before the dimensional redesign shipped.';

-- PostgREST schema reload so the new columns are queryable without a
-- service restart (mandatory per project_jerry_server_side memory).
NOTIFY pgrst, 'reload schema';
