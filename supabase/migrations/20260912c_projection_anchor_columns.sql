-- 2026-09-13 Market-anchor projection layer (NFL + NCAAF).
--
-- Andy Week 1 audit exposed that thin-sample model output on football
-- (stats_source = 'prior_season_regressed') produces hallucinations like
-- "ARI by 2 vs LAC" when the underlying team-stat inputs are regressed
-- toward league mean. Fix pattern: blend projected_spread toward market
-- spread by a magnitude-tiered weight — small disagreements stay pure
-- model (real edge zone), large disagreements anchor hard to market
-- (hallucination gate). Weight schedule lives in mlb_pipeline/
-- projection_anchor.py.
--
-- These three columns preserve the raw model output and audit trail so
-- we can grade both raw + anchored after Wk 1 completes and learn which
-- weight tiers should evolve for Wk 2+.
--
-- Applies to both nfl_game_context and ncaaf_game_context — same anchor
-- design works cross-sport since both share the stats_source pattern.

ALTER TABLE nfl_game_context
    ADD COLUMN IF NOT EXISTS projected_spread_raw   numeric,
    ADD COLUMN IF NOT EXISTS spread_anchor_weight   numeric,
    ADD COLUMN IF NOT EXISTS spread_anchor_reason   text;

ALTER TABLE ncaaf_game_context
    ADD COLUMN IF NOT EXISTS projected_spread_raw   numeric,
    ADD COLUMN IF NOT EXISTS spread_anchor_weight   numeric,
    ADD COLUMN IF NOT EXISTS spread_anchor_reason   text;

NOTIFY pgrst, 'reload schema';
