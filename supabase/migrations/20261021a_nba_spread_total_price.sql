-- ══ 2026-10-21 · CAPTURE THE JUICE WE ALREADY FETCH AND THROW AWAY ══
-- QA on the GSW @ LAL card: "Odds tiles are missing prices. The spread and
-- total tiles show '—' where the juice (-110) should be."
--
-- The dash was honest: no sport stores a spread or total price anywhere.
-- Checked across nba/nfl/mlb_game_context — the only price-ish columns are
-- home_ml_odds / away_ml_odds and the oddscrowd snapshot.
--
-- But the number is already in hand and discarded. nba_game_context.py reads
-- the Odds API outcome block as
--
--     price = o.get('price')
--     point = o.get('point')
--     if key == 'h2h':      ... uses price ...
--     elif key == 'spreads': ... uses point ONLY ...
--     elif key == 'totals':  ... uses point ONLY ...
--
-- so the juice is parsed on every pull and dropped on the floor for two of
-- the three markets.
--
-- WHY THIS IS NOT A COSMETIC TILE FIX. Without a stored price, every spread
-- and total ROI in the app is computed at an ASSUMED -110. That assumption is
-- already known to be worth a lot: the Steam Room record published at flat
-- -110 reads +79.91u while the same picks at real prices are +3.25u. Spreads
-- and totals are most of what we publish, and we have been grading them at a
-- price nobody was offered.
--
-- NBA ONLY in this migration, deliberately. Each sport's builder parses its
-- own odds block differently (NFL and NCAAF go through separate pullers with
-- their own market loops), so wiring them is a per-sport change and shipping
-- a column nobody writes is how a table grows dead weight. NBA is wired in
-- the same commit as this file; the others are logged.
--
-- Nullable with no default: a missing price must read as MISSING, never as
-- -110. That is the whole point.

ALTER TABLE nba_game_context
  ADD COLUMN IF NOT EXISTS close_spread_home_price integer,
  ADD COLUMN IF NOT EXISTS close_spread_away_price integer,
  ADD COLUMN IF NOT EXISTS close_total_over_price  integer,
  ADD COLUMN IF NOT EXISTS close_total_under_price integer;

COMMENT ON COLUMN nba_game_context.close_spread_home_price IS
  'American odds on the HOME side of close_spread, as posted. NULL means we '
  'never captured one - do NOT substitute -110, that assumption is what makes '
  'a flat-priced record disagree with the real one.';
COMMENT ON COLUMN nba_game_context.close_total_over_price IS
  'American odds on the OVER at close_total, as posted. NULL means missing.';

NOTIFY pgrst, 'reload schema';
