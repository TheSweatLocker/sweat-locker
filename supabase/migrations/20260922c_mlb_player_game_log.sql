-- 2026-09-22 · Persist MLB player game logs
--
-- Andy: "so we wasted an entire season of mlb data? we have no data we
-- could use, wtf have we been doing all season?"
--
-- Not wasted, and worth being precise about it. Two things are true:
--
--   1. mlb_pipeline_props.final_value already holds 24,048 real outcomes
--      across 785 players and 148 dates (2026-04-24 → 09-21). That is a
--      genuine dataset. It is SPARSE — it only covers players who drew a
--      prop, roughly 49 player-games a day against a slate of ~270.
--   2. The complete season was never ours to lose. MLB Stats API is free
--      and public and has every boxscore. Nothing was destroyed; it was
--      simply never written down.
--
-- What we actually skipped is persistence. Every batter signal —
-- L5/L10 form, season hit rate, streaks — was computed from a LIVE
-- gameLog fetch at generation time and then discarded. Two consequences,
-- both of which cost real money:
--
--   * The lookback leak (20b443cc). A live fetch with no upper date
--     bound put the game being predicted inside its own L5/L10 window.
--     A stored, date-stamped log makes that leak structurally
--     impossible rather than guarded against.
--   * Nothing can be backtested. There is no way to ask "what did this
--     model know on 2026-07-14" when the inputs were never saved, which
--     is why the prop tier ladder ran a whole season without anyone able
--     to measure it against the closing price.
--
-- WHY NOT THE EXISTING player_game_log TABLE. That one is prop-shaped —
-- prop_type, line, hit, result_value. It records whether a PROP hit, not
-- what a PLAYER did. Deriving a projection needs the stat line itself,
-- so any prop family can be computed from it rather than only the ones
-- someone happened to post a line for. It is also empty (0 rows), so
-- nothing is lost by leaving it alone.
--
-- One table covers batters and pitchers. The prop families we grade map
-- straight onto these columns:
--
--   total_bases -> tb      hits -> h        rbis -> rbi     hr -> hr
--   runs -> r              batter_ks -> so
--   ks -> p_so             bb -> p_bb       ha -> p_h
--   outs -> outs           er -> er
--
-- UNIQUE (player_id, game_pk) makes the backfill idempotent and safe to
-- resume — it runs ~2,400 boxscore calls and will be interrupted.

BEGIN;

CREATE TABLE IF NOT EXISTS public.mlb_player_game_log (
  id             BIGSERIAL PRIMARY KEY,
  player_id      INTEGER     NOT NULL,
  player_name    TEXT        NOT NULL,
  team           TEXT,
  opponent       TEXT,
  game_pk        INTEGER     NOT NULL,
  game_date      DATE        NOT NULL,
  home_away      TEXT,
  pos            TEXT,
  batting_order  INTEGER,          -- 100 = leadoff; NULL = did not bat
  is_starter     BOOLEAN,

  -- batting
  pa      INTEGER, ab      INTEGER, h   INTEGER, doubles INTEGER,
  triples INTEGER, hr      INTEGER, rbi INTEGER, r       INTEGER,
  bb      INTEGER, so      INTEGER, tb  INTEGER, sb      INTEGER,
  hbp     INTEGER, sf      INTEGER,

  -- pitching
  outs    INTEGER, ip   NUMERIC, p_h  INTEGER, p_r     INTEGER,
  er      INTEGER, p_bb INTEGER, p_so INTEGER, bf      INTEGER,
  pitches INTEGER,

  source      TEXT        NOT NULL DEFAULT 'mlb_statsapi',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT mlb_player_game_log_uniq UNIQUE (player_id, game_pk)
);

-- The access pattern this exists for: "this player's games strictly
-- before date X". Without the date in the index every as-of-date
-- lookback scans the player's whole career.
CREATE INDEX IF NOT EXISTS mlb_pgl_player_date
  ON public.mlb_player_game_log (player_id, game_date DESC);
CREATE INDEX IF NOT EXISTS mlb_pgl_name_date
  ON public.mlb_player_game_log (player_name, game_date DESC);
CREATE INDEX IF NOT EXISTS mlb_pgl_date
  ON public.mlb_player_game_log (game_date DESC);

ALTER TABLE public.mlb_player_game_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS mlb_pgl_read ON public.mlb_player_game_log;
CREATE POLICY mlb_pgl_read ON public.mlb_player_game_log
  FOR SELECT USING (true);

DROP POLICY IF EXISTS mlb_pgl_write ON public.mlb_player_game_log;
CREATE POLICY mlb_pgl_write ON public.mlb_player_game_log
  FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMIT;

NOTIFY pgrst, 'reload schema';

-- VERIFY after running backfill_mlb_player_game_log.py:
--   SELECT COUNT(*), MIN(game_date), MAX(game_date),
--          COUNT(DISTINCT player_id), COUNT(DISTINCT game_pk)
--     FROM mlb_player_game_log;
-- Expect roughly 2,400 games and 120k+ rows for a full 2026 season.
--
-- Then the cross-check that matters — the backfill must agree with the
-- outcomes we already independently recorded:
--   compare mlb_player_game_log.h / tb / hr / rbi / r against
--   mlb_pipeline_props.final_value on the same (player_name, game_date).
