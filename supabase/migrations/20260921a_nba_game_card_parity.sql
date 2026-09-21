-- 2026-09-21 — NBA game-card parity with the other sports.
--
-- Andy: "minimal client side is the priority when wiring up anything."
--
-- THE PROBLEM THIS CLOSES
-- app/index.tsx carries ~106 lines of CLIENT-SIDE NBA scoring
-- (`modelMismatch` appears 66 times) that computes its own net-rating /
-- eFG / defensive-rating / back-to-back model from a separate nbaContext
-- fetch. MLB scoring moved server-side long ago — index.tsx:4028 says so
-- explicitly — but NBA never did, and nba_game_context has no
-- sweat_score / sweat_tier columns for the client to read even if it
-- wanted to.
--
-- So on 2026-10-21, when NBA context starts populating, the client and
-- the server would produce two different numbers for the same game. The
-- server has to own the number first; only then can the client block be
-- deleted.
--
-- Column set mirrors nhl_game_context, which already has all of these.
-- Comparison taken 2026-09-21:
--            NHL  NBA
--   sweat_score         ✓    ✗
--   sweat_tier          ✓    ✗
--   signal_confluence_net ✓  ✗
--   mc_probabilities    ✓    ✗
--   oddscrowd_snapshot  ✓    ✗
--   open_spread         ✗    ✓   (NHL gap, added here too)

ALTER TABLE nba_game_context
  ADD COLUMN IF NOT EXISTS sweat_score            integer,
  ADD COLUMN IF NOT EXISTS sweat_tier             text,
  ADD COLUMN IF NOT EXISTS sweat_tier_current     text,
  ADD COLUMN IF NOT EXISTS sweat_breakdown        jsonb,
  ADD COLUMN IF NOT EXISTS signal_confluence_net  integer,
  ADD COLUMN IF NOT EXISTS signal_confluence_breakdown jsonb,
  ADD COLUMN IF NOT EXISTS mc_probabilities       jsonb,
  ADD COLUMN IF NOT EXISTS oddscrowd_snapshot     jsonb,
  ADD COLUMN IF NOT EXISTS splits_summary         jsonb,
  ADD COLUMN IF NOT EXISTS align_status           text;

-- NHL is missing the opening line, which the other sports carry and the
-- line-movement view needs to show drift from open to close.
ALTER TABLE nhl_game_context
  ADD COLUMN IF NOT EXISTS open_spread   numeric,
  ADD COLUMN IF NOT EXISTS open_total    numeric,
  ADD COLUMN IF NOT EXISTS align_status  text;

COMMENT ON COLUMN nba_game_context.sweat_score IS
  'Server-computed 0-100 composite. THE source of truth for the app — '
  'the client must render this, never recompute it. Written by '
  'nba_game_context.compute_sweat_score.';
COMMENT ON COLUMN nba_game_context.signal_confluence_net IS
  'Positive = home leans, negative = away. Count of home-leaning signals '
  'minus away-leaning, from net rating / pace / rest / B2B / injury '
  'impact / elo. Feeds sweat_score.';

NOTIFY pgrst, 'reload schema';
