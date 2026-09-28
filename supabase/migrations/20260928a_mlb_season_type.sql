-- 2026-09-28a · mlb_game_context.season_type
--
-- Andy: "expectation for MLB playoffs, will Jerry know when writing up."
--
-- It did not, and nothing else did either. mlb_game_context carries 321
-- columns and NOT ONE of them is season_type, while nfl_game_context and
-- ncaaf_game_context both have it. Nothing in the MLB path — game_context.py,
-- generate_jerry_synthesis.py, jerry_anchor_potd.py — mentions postseason or
-- playoff anywhere.
--
-- The games arrive regardless: the slate comes from the Odds API, which
-- already prices all four Wild Card series, and no regular-season filter
-- exists anywhere in the pipeline. So on 2026-09-29 four elimination games
-- would have been ingested, modelled and written up as ordinary regular
-- season games. Present, and silently mis-modelled.
--
-- Jerry is handed this at runtime by mlb_season_type.py (looked up live from
-- StatsAPI gameType) and does not depend on this column. The column is for
-- everything that needs it PERSISTED and QUERYABLE:
--   * grading and surface records, so an October record can be separated
--     from a June one rather than blended into a single season number
--   * cohort and pattern backtests, which are regular-season-trained and
--     should be able to EXCLUDE postseason rather than quietly absorb it
--   * any future guard that wants to cap tier in the playoffs
--
-- VALUES mirror the helper: REGULAR, WILDCARD, DIVISION_SERIES, LCS,
-- WORLD_SERIES, SPRING, EXHIBITION, ALL_STAR.
--
-- DEFAULT 'REGULAR' rather than NULL, deliberately. A null would make every
-- historical row ambiguous between "regular season" and "not yet
-- classified", and the far more common case by orders of magnitude is
-- regular season. The failure mode we want is a playoff game mislabelled as
-- normal, never a June game mislabelled as special.

ALTER TABLE public.mlb_game_context
  ADD COLUMN IF NOT EXISTS season_type text NOT NULL DEFAULT 'REGULAR';

-- series_game / games_in_series: "game 2 of a best-of-three" is a materially
-- different spot from game 1 — elimination pressure, bullpen availability
-- after a short-rest start — and StatsAPI gives both for free.
ALTER TABLE public.mlb_game_context
  ADD COLUMN IF NOT EXISTS series_game smallint;

ALTER TABLE public.mlb_game_context
  ADD COLUMN IF NOT EXISTS games_in_series smallint;

-- Partial index: postseason rows are a tiny fraction of the table, and every
-- query that wants them wants exactly them.
CREATE INDEX IF NOT EXISTS idx_mlb_game_context_postseason
  ON public.mlb_game_context (game_date)
  WHERE season_type <> 'REGULAR';

COMMENT ON COLUMN public.mlb_game_context.season_type IS
  'MLB StatsAPI gameType decoded: REGULAR | WILDCARD | DIVISION_SERIES | '
  'LCS | WORLD_SERIES | SPRING | EXHIBITION | ALL_STAR. Written by '
  'mlb_season_type.py. Defaults REGULAR so a lookup failure never makes an '
  'ordinary game look like a playoff game.';

NOTIFY pgrst, 'reload schema';
