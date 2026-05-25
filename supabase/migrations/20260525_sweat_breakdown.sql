-- Sweat-score breakdown for MLB game-detail UI parity with NBA (2026-05-25).
--
-- NBA's game-detail modal renders a "WHY THIS SCORE" block with weighted
-- contributions (each signal that drove the score, with point amounts) and
-- supporting evidence (context items, no point claim). MLB's server-scored
-- path bypasses the legacy client formula so it never produced these arrays
-- — the WHY THIS SCORE block stayed hidden on MLB games, breaking parity.
--
-- This column stores both arrays as JSONB. play_of_day.score_mlb_game now
-- tracks them while computing the score. The app reads from here in the
-- server-scored MLB path of getSweatScoreForGame.
--
-- Forward-looking only — existing rows stay null; frontend's gate already
-- handles missing breakdown gracefully (falls back to MODEL'S PLAY card
-- without contributions).
--
-- Shape (illustrative):
-- {
--   "contributions": [
--     {"emoji": "⚾", "label": "NRFI sweet spot", "points": 30, "detail": "Score 94/100 — audit 71.4% n=28"},
--     {"emoji": "🎯", "label": "Strong confluence", "points": 10, "detail": "4 signals on one side"}
--   ],
--   "evidence": [
--     {"emoji": "🏟", "label": "Pitcher-friendly park", "detail": "Park factor 93"},
--     {"emoji": "❄️", "label": "Cold opp offense", "detail": "L10 R/G 3.1 vs season 4.06"}
--   ]
-- }

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS sweat_breakdown jsonb;

-- PostgREST schema cache reload (per feedback_migration_pgrst_reload).
NOTIFY pgrst, 'reload schema';
