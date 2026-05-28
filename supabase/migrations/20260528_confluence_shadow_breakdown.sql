-- Shadow-signal column on mlb_game_context for the Tier 1 backtest queue.
--
-- 5/28 launch-blocker work — adds shadow signals for catcher framing (in
-- props), travel fatigue, long-rest-pitcher volatility, and inning-1 team
-- stats (pending enrichment). Stored separately from signal_confluence_
-- breakdown so they are visible per-game but DO NOT alter live tier/
-- conviction. After 1-2 weeks of resolved games we join shadow_breakdown
-- against actual outcomes to validate each before promoting to live signals.

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS signal_confluence_shadow_breakdown JSONB;

NOTIFY pgrst, 'reload schema';
