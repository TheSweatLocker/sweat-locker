-- == 2026-10-21 - THE STEAM ROOM READ 6,000 ROWS TO GET 30 STRINGS ==
--
-- B119 residual. Opening the Steam Room fired, per visit:
--
--   1 query   line_movement_flags (120 rows)
--   30 queries line_history, each .limit(200) over a 48-hour window
--   3 queries  *_game_context
--
-- Thirty round trips and up to 6,000 rows. The code's own comment says the
-- sample is "a small time-series sample from line_history so we can render a
-- compact sparkline inline."
--
-- THERE IS NO SPARKLINE. Grepping every use of that prop in
-- LineMovementTab.tsx: `sample[0]?.matchup` at lines 415 and 590, and
-- `sample[0]?.commence_time` at 594. Nothing reads index 1 or beyond and
-- nothing plots anything. Two hundred rows per series are fetched, 199 are
-- discarded, and the one that is kept is used for a TEXT LABEL.
--
-- ── WHY AN RPC RATHER THAN ONE BATCHED SELECT ───────────────────────────
-- The obvious fix is `.in('game_id', gids)` with a bigger limit, and that
-- walks into the trap this project has hit repeatedly: PostgREST orders then
-- truncates, so ONE limit across many series starves whichever series sort
-- last. That is how a batched matchup lookup rendered a card titled
-- `2026020164` on 10-21, and it is the same shape as the 1000-row cap found
-- in 20+ scripts.
--
-- DISTINCT ON removes the guess. One row per (game_id, market) by
-- construction, so there is no limit to size and no series that can come
-- back empty because of its position in a sort.
--
-- Takes the NEWEST capture rather than the oldest the client used to read.
-- A matchup string does not change over a game's line history, so either
-- works for the label - but the newest row is the one most likely to exist
-- at all for a game flagged minutes ago.
--
-- ── AND A SECOND SILENT FALLBACK WAS ALREADY BROKEN ─────────────────────
-- LineMovementCard reads `sample[0]?.commence_time` as its kickoff
-- fallback. The client's select was
-- `side,book,line,price,captured_at,matchup` - commence_time was never
-- asked for, so that expression has ALWAYS been undefined. The column does
-- exist on line_history and carries a real value
-- ('2026-10-08T21:00:00+00:00' on the row probed today), so the fallback
-- was one word away from working for however long it has been there. Same
-- defect as the 2026-08-22 note directly above it in the client, which
-- added `matchup` to this very select for exactly this reason and did not
-- notice its neighbour. Returned here.
--
-- Pairs are passed as JSONB rather than two parallel arrays because
-- `game_id IN (...) AND market IN (...)` is a CROSS PRODUCT: 20 games and 3
-- markets asks for 60 combinations when 30 were flagged, and every spurious
-- combination is a row the client then has to throw away.

CREATE OR REPLACE FUNCTION public.get_steam_matchup_labels(
  p_pairs JSONB,
  p_hours INT DEFAULT 48
)
RETURNS TABLE (
  game_id       TEXT,
  market        TEXT,
  matchup       TEXT,
  commence_time TIMESTAMPTZ,
  captured_at   TIMESTAMPTZ
)
LANGUAGE sql
STABLE
AS $$
  SELECT DISTINCT ON (h.game_id, h.market)
         h.game_id, h.market, h.matchup, h.commence_time, h.captured_at
    FROM public.line_history h
    JOIN LATERAL (
      SELECT p->>'game_id' AS gid, p->>'market' AS mkt
        FROM jsonb_array_elements(p_pairs) AS p
    ) q ON q.gid = h.game_id AND q.mkt = h.market
   WHERE h.captured_at >= now() - make_interval(hours => GREATEST(p_hours, 1))
   ORDER BY h.game_id, h.market, h.captured_at DESC;
$$;

COMMENT ON FUNCTION public.get_steam_matchup_labels(JSONB, INT) IS
  'One line_history row per (game_id, market) pair, newest first, for the '
  'Steam Room''s matchup label. Replaces 30 client queries of 200 rows each '
  'that existed to read sample[0].matchup - no sparkline was ever built. '
  'DISTINCT ON rather than a batched limit, so no series can be starved by '
  'where it sorts.';

GRANT EXECUTE ON FUNCTION public.get_steam_matchup_labels(JSONB, INT)
  TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
