-- 2026-07-29: add market-odds columns to ufc_picks so the scorer can
-- compute EV per fight against real bookmaker prices (Odds API h2h).
--
-- Method/distance/round market lines are not exposed by Odds API's MMA
-- endpoint at our tier (all returned 422). When we add a scrape or
-- upgrade, extend with method_ko_odds_over etc.

ALTER TABLE ufc_picks
    ADD COLUMN IF NOT EXISTS odds_a_median  numeric,   -- decimal odds, side A
    ADD COLUMN IF NOT EXISTS odds_b_median  numeric,   -- decimal odds, side B
    ADD COLUMN IF NOT EXISTS odds_a_best    numeric,   -- best-price shopped
    ADD COLUMN IF NOT EXISTS odds_b_best    numeric,
    ADD COLUMN IF NOT EXISTS odds_book_count integer,
    ADD COLUMN IF NOT EXISTS odds_pulled_at timestamptz,
    ADD COLUMN IF NOT EXISTS ev_side_a      numeric,   -- EV per $100 bet on A (median odds)
    ADD COLUMN IF NOT EXISTS ev_side_b      numeric,
    ADD COLUMN IF NOT EXISTS ev_tier        text,      -- PRIME / STRONG / LEAN / SKIP based on best-EV side
    ADD COLUMN IF NOT EXISTS ev_recommended_side text; -- 'a' | 'b' | null

COMMENT ON COLUMN ufc_picks.odds_a_median IS 'Median decimal odds across books for fighter A ML';
COMMENT ON COLUMN ufc_picks.ev_side_a IS 'Model prob × payout − (1−prob) × 100. Positive = +EV.';
COMMENT ON COLUMN ufc_picks.ev_tier IS 'PRIME (EV≥+8) STRONG (+4-7) LEAN (+2-3) SKIP (<+2)';

NOTIFY pgrst, 'reload schema';
