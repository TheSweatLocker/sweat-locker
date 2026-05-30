-- 2026-05-30 — L10 W-L snapshot columns for Jerry Model + backtest.
--
-- Jerry Model's L10 momentum multiplier (5/30 add per user direction)
-- needs per-team L10 W-L preserved at game time. The MLB Stats API
-- enrichment already populates home_last10 / away_last10 STRINGS
-- ("8-2", "2-8") on mlb_game_context, but mlb_game_results doesn't
-- preserve them and the strings are awkward to query against.
--
-- Adding parsed integer columns to both tables so:
--   1. Live Jerry reads home_l10_wins / home_l10_losses directly
--   2. Backtest joins on these clean integers without re-parsing strings
--   3. Future audit queries (e.g. "Jerry hit rate when team L10 win% > .700")
--      have a clean predicate column.

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS home_l10_wins SMALLINT,
    ADD COLUMN IF NOT EXISTS home_l10_losses SMALLINT,
    ADD COLUMN IF NOT EXISTS away_l10_wins SMALLINT,
    ADD COLUMN IF NOT EXISTS away_l10_losses SMALLINT;

ALTER TABLE mlb_game_results
    ADD COLUMN IF NOT EXISTS home_l10_wins SMALLINT,
    ADD COLUMN IF NOT EXISTS home_l10_losses SMALLINT,
    ADD COLUMN IF NOT EXISTS away_l10_wins SMALLINT,
    ADD COLUMN IF NOT EXISTS away_l10_losses SMALLINT;

COMMENT ON COLUMN mlb_game_context.home_l10_wins IS
  'Home team last-10-games wins as of game time. Parsed from MLB Stats API.
   Used by Jerry Model momentum multiplier — different signal from
   offense_drift (which is offense-only) because L10 W-L captures pitching,
   clutch, and overall team momentum.';

NOTIFY pgrst, 'reload schema';
