-- 2026-09-26 · Start accumulating team-stat history.
--
-- Andy approved after study_stat_edge came back 0-for-30: the five stats we
-- CAN backtest leak-free (ppg, points allowed, margin, ATS rate, over rate)
-- carry no ATS edge across 16,736 games. The stats most likely to carry one —
-- EPA, success rate, explosiveness, SP+ — could not be tested at all, because
-- team_stats_rolling is a MATERIALIZED VIEW holding only the CURRENT value.
-- Every row shares one refreshed_at and only the live season exists.
--
-- That history is not recoverable. It was never written down. The only thing
-- that changes the answer next season is starting to write it down today,
-- which is all this table does.
--
-- WHY A TABLE AND NOT A SECOND MATVIEW: a matview is rebuilt from source on
-- every REFRESH, so it can only ever show "now". History has to be appended
-- and then left alone. Note the same trap already bit team_situational_records
-- (see 20260916a) — a DELETE there was undone by the next refresh, which is
-- why that fix had to rename the MV and expose a filtered view instead.
--
-- CHANGE-ONLY WRITES. snapshot_team_stats.py appends a row only when a stat's
-- (raw_value, rank) actually differs from that key's most recent snapshot.
-- A naive daily full copy would be 9,658 rows/day ≈ 3.5M/year, and most of it
-- would be duplicates: football stats only move once a week, and an offseason
-- sport like NCAAB (4,523 rows, 47% of the table) does not move for months.
-- Change-only keeps the as-of-date query identical — "latest snapshot <= D" —
-- while storing a small fraction of the rows.
--
-- READING IT BACK, the pattern that must stay leak-free:
--     SELECT DISTINCT ON (team, stat_key) team, stat_key, raw_value, rank
--       FROM team_stats_rolling_history
--      WHERE sport = $1 AND season = $2 AND snapshot_date < $game_date
--      ORDER BY team, stat_key, snapshot_date DESC;
-- Note `<` and not `<=`. A snapshot taken ON game day may already include
-- that game's result, which is exactly the 2026-09-22 lookback leak that was
-- worth 6-7pp of fabricated edge.

CREATE TABLE IF NOT EXISTS public.team_stats_rolling_history (
    id            BIGSERIAL PRIMARY KEY,
    snapshot_date DATE    NOT NULL,
    sport         TEXT    NOT NULL,
    team          TEXT    NOT NULL,
    season        INTEGER NOT NULL,
    stat_key      TEXT    NOT NULL,
    raw_value     NUMERIC,
    rank          INTEGER,
    league_size   INTEGER,
    direction     TEXT,
    display_label TEXT,
    unit          TEXT,
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT team_stats_rolling_history_uq
        UNIQUE (sport, team, season, stat_key, snapshot_date)
);

-- The as-of-date lookup above: filter by sport/season, then walk back per
-- (team, stat_key). DESC on snapshot_date so DISTINCT ON takes the newest.
CREATE INDEX IF NOT EXISTS idx_tsrh_asof
    ON public.team_stats_rolling_history
       (sport, season, team, stat_key, snapshot_date DESC);

-- Coverage/health checks: "how many rows did we capture per sport per day."
CREATE INDEX IF NOT EXISTS idx_tsrh_coverage
    ON public.team_stats_rolling_history (sport, snapshot_date);

-- Internal analysis table — no client reads it. RLS on with no policy means
-- service_role only (service_role bypasses RLS), which is the safe default
-- and cannot regress the anon surface.
ALTER TABLE public.team_stats_rolling_history ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.team_stats_rolling_history IS
    'Append-only history of team_stats_rolling. Change-only writes from '
    'snapshot_team_stats.py. Read as-of-date with snapshot_date < game_date '
    '(strictly less — a same-day snapshot can contain that game''s result).';

NOTIFY pgrst, 'reload schema';
