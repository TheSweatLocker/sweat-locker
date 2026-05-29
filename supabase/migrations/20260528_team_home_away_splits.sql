-- Team home/away offensive splits on mlb_team_offense.
--
-- Tier 1 #9 (5/28). Currently every offensive stat (OPS, wRC+, RPG) on
-- mlb_team_offense is venue-agnostic — same number applies whether the
-- team is at home or on the road. Some teams have dramatic splits
-- (Coors-bound Rockies obvious example, but also teams like the
-- Athletics whose offensive park-effect is bigger than realized).
-- Without home/away splits the spread/total model treats all venues
-- the same.
--
-- Populated by enrich_team_recency.py — same MLB Stats API gameLog
-- already pulled for L7/L14 recency, just filtered by sp['isHome']
-- before aggregation. Zero extra API cost.
--
-- These columns are the source-of-truth at team level. game_context.py
-- will pull them per game (home team's home_ops, away team's away_ops)
-- as a SHADOW signal in confluence — only counted toward net once
-- backtested (Tier 1 promotion gate).

ALTER TABLE mlb_team_offense
    ADD COLUMN IF NOT EXISTS home_ops_split        NUMERIC(4,3),
    ADD COLUMN IF NOT EXISTS away_ops_split        NUMERIC(4,3),
    ADD COLUMN IF NOT EXISTS home_wrc_proxy_split  NUMERIC(5,1),
    ADD COLUMN IF NOT EXISTS away_wrc_proxy_split  NUMERIC(5,1),
    ADD COLUMN IF NOT EXISTS home_games_played     INTEGER,
    ADD COLUMN IF NOT EXISTS away_games_played     INTEGER;

NOTIFY pgrst, 'reload schema';
