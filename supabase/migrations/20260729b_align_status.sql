-- 2026-07-29b: Alignment layer for game-detail redesign.
--
-- Two computed jsonb columns per game so the app can render:
--   1. Money Flow section (bets%/money% bars per market) → oddscrowd_snapshot
--   2. Alignment status strip + ALIGNED badge → align_status
--
-- Both populated by mlb_pipeline/compute_align_status.py which reads
-- external_picks (all sources incl. oddscrowd) + the game_context lens
-- fields and writes the two blobs back.
--
-- Shape (documented, not enforced — jsonb):
--
-- oddscrowd_snapshot: {
--   "ml":    {"pick": "HOME", "money": 72, "bets": 73, "div": -1, "fade": "neutral"},
--   "rl":    {"pick": "HOME", "money": 98, "bets": 25, "div": 73,  "fade": "boost"},
--   "total": {"pick": "OVER", "money": 69, "bets": 68, "div":  1, "fade": "neutral"},
--   "pulled_at": "2026-07-29T18:12:03Z"
-- }
--
-- align_status: {
--   "ml": {
--     "ext_lead": "H", "ext_count": 4, "ext_total": 5,
--     "money_side": "H", "money_pct": 72, "bets_pct": 73, "div": -1,
--     "lens_side": null, "lens_count": null, "lens_total": null,
--     "aligned": true, "verdict": "aligned_strong"  -- aligned_strong | aligned | disagreement | no_data
--   },
--   "rl":    {...},
--   "total": {...},
--   "overall": {"aligned": true, "verdict": "aligned_strong", "aligned_markets": 2},
--   "computed_at": "2026-07-29T18:12:03Z"
-- }
--
-- Migration only replicates for MLB. Sport-agnostic version (NFL/NCAAF/
-- NCAAB/NBA/UFC contexts) added when compute script extends.

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS oddscrowd_snapshot jsonb,
    ADD COLUMN IF NOT EXISTS align_status       jsonb;

ALTER TABLE nfl_game_context
    ADD COLUMN IF NOT EXISTS oddscrowd_snapshot jsonb,
    ADD COLUMN IF NOT EXISTS align_status       jsonb;

ALTER TABLE ncaaf_game_context
    ADD COLUMN IF NOT EXISTS oddscrowd_snapshot jsonb,
    ADD COLUMN IF NOT EXISTS align_status       jsonb;

ALTER TABLE ncaab_game_context
    ADD COLUMN IF NOT EXISTS oddscrowd_snapshot jsonb,
    ADD COLUMN IF NOT EXISTS align_status       jsonb;

COMMENT ON COLUMN mlb_game_context.oddscrowd_snapshot IS
    'Per-market money%/bets% snapshot from oddscrowd. Populated by compute_align_status.py after each externals pull.';
COMMENT ON COLUMN mlb_game_context.align_status IS
    'Alignment verdict per market (ML/RL/Total) + overall — combines external consensus, oddscrowd money side, and model lens direction.';

NOTIFY pgrst, 'reload schema';
