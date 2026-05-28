-- Extend projected_ks pattern to every pitcher prop (BB / Hits / Outs / ER)
-- plus WHIP for narrative color.
--
-- Companion to 20260527_projected_ks_columns.sql. The K-Over single-source-
-- of-truth was step 1; this generalizes the pattern so the same data-quality
-- gap can't open on any other pitcher prop. All five values come from the
-- same l7_rolling JSON cache that the prop scorers already use.
--
-- Populated by mlb_pipeline/patch_projected_ks.py (extended) so Jerry
-- struct, Numbers panel, and social copy all read the SAME number the
-- BB-Under / Hits-Under / Outs / ER prop scorers cite.

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS home_pitcher_projected_bb    NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS away_pitcher_projected_bb    NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS home_pitcher_projected_hits  NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS away_pitcher_projected_hits  NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS home_pitcher_projected_outs  NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS away_pitcher_projected_outs  NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS home_pitcher_projected_er    NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS away_pitcher_projected_er    NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS home_pitcher_whip            NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS away_pitcher_whip            NUMERIC(4,2);

NOTIFY pgrst, 'reload schema';
