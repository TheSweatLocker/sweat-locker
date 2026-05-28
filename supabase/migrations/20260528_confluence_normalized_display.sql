-- Normalize the confluence display denominators.
--
-- Trigger: 5/28 audit revealed signals_voted varies per game (5 to 9
-- depending on data availability), so "all signals agree" comes out as
-- "6/6" on one game and "9/9" on another. Same message, different
-- denominator, confused user trust.
--
-- Fix: surface BOTH the voted count AND the canonical total signal
-- universe so the app can render consistently — e.g., "9 of 14 signals
-- voted (5 home / 4 away / 5 no data)".

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS signal_confluence_signals_voted INTEGER,
    ADD COLUMN IF NOT EXISTS signal_confluence_signals_total INTEGER;

NOTIFY pgrst, 'reload schema';
