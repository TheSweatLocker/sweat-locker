-- 20261009a · NHL props cannot join their own game (the hash/numeric split)
--
-- nhl_pipeline_props.game_id is an MD5 hash (841087bae12b56c16406af177d78ea01)
-- while nhl_game_context.game_id is the NHL numeric id (2026020078). Two ID
-- spaces, so every query that filters props by the game's id returns ZERO
-- rows. That is why game detail cannot surface NHL props — the panel is handed
-- the numeric id and the props carry a hash. See
-- project_nhl_props_cannot_join_their_game_1003.
--
-- WHY A SECOND COLUMN AND NOT A REWRITE OF game_id:
-- public_receipts and existing joins key on the hash. Rewriting a published
-- identity is the set-once trap (trg_freeze_receipt_identity blocks
-- value -> value, and anything already emitted would be orphaned). An
-- additive column is reversible and breaks nothing that works today.
--
-- Populated by mlb_pipeline/bridge_nhl_prop_game_ids.py, which resolves
-- (game_date, unordered team_abbrev/opp_abbrev pair) -> context game and
-- REFUSES to guess when a row does not resolve to exactly one game. That
-- match is safe because the distinct prop-game count per date exactly equals
-- the context-game count per date on every date props exist (5/5, 8/8, 13/13,
-- 10/10, ...), which makes the mapping 1:1.

ALTER TABLE public.nhl_pipeline_props
  ADD COLUMN IF NOT EXISTS ctx_game_id text;

COMMENT ON COLUMN public.nhl_pipeline_props.ctx_game_id IS
  'NHL numeric game id matching nhl_game_context.game_id. game_id on this '
  'table is an MD5 hash in a different ID space and cannot join. Populated '
  'by bridge_nhl_prop_game_ids.py; see migration 20261009a.';

-- The client filters props for one game by this column, so it needs the index
-- rather than a sequential scan over 26k rows on every game-detail open.
CREATE INDEX IF NOT EXISTS idx_nhl_props_ctx_game_id
  ON public.nhl_pipeline_props (ctx_game_id)
  WHERE ctx_game_id IS NOT NULL;

-- Game detail filters on (ctx_game_id, game_date) together.
CREATE INDEX IF NOT EXISTS idx_nhl_props_ctx_game_date
  ON public.nhl_pipeline_props (ctx_game_id, game_date)
  WHERE ctx_game_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
