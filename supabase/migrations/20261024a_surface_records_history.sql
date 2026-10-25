-- == 2026-10-24a · A PUBLISHED RECORD CAN MOVE AND NOTHING NOTICES ==
--
-- On 10-23 the all-time Sharp went +81.21u -> +36.77u overnight. The W-L
-- barely moved; only the pricing basis changed. No alert fired, nothing was
-- logged, and the only detector in the system was Andy reading the card the
-- next morning and asking why the units were half what they had been.
--
-- The reason it was undetectable AFTER the fact is this: surface_records
-- holds exactly one row per (surface, sport, window_key), UPDATED in place by
-- compute_surface_records.py. There is no history anywhere. So once the
-- number moved, the previous value was gone -- it could not be diffed, and
-- the size of the move could not even be confirmed except from a code comment
-- that happened to record it.
--
-- This table is the missing memory. One snapshot per surface/sport/window per
-- ET day, so day-over-day movement becomes measurable, and a watchdog can ask
-- the question that matters: did units move by MORE than the newly-graded
-- picks could account for? A record that moves without new results is either
-- a methodology change or a bug, and both need to be said out loud before a
-- subscriber finds them.
--
-- Deliberately a snapshot table and not an audit trigger. The writer runs a
-- handful of times a day, a daily grain is all the resolution the question
-- needs, and a trigger on every UPDATE would store thousands of identical
-- rows for the same answer.
--
-- snapshot_date is ET, not UTC. A UTC day boundary is 8pm ET, so a
-- UTC-stamped daily snapshot would split one slate across two dates and
-- silently drop the oldest day of any window computed from it -- the same
-- defect as feedback_window_from_runner_clock_is_a_day_ahead.

CREATE TABLE IF NOT EXISTS public.surface_records_history (
    id              bigserial PRIMARY KEY,
    snapshot_date   date        NOT NULL,
    captured_at     timestamptz NOT NULL DEFAULT now(),
    surface         text        NOT NULL,
    sport           text        NOT NULL,
    window_key      text        NOT NULL,
    wins            integer,
    losses          integer,
    pushes          integer,
    picks_count     integer,
    hit_rate        numeric,
    units_net       numeric,
    roi_pct         numeric,
    epoch_start     date,
    last_pick_date  date,
    -- what compute_surface_records believed when it wrote the live row, so a
    -- diff can separate "we recomputed" from "a new day of results landed"
    source_computed_at timestamptz
);

-- One snapshot per key per ET day. A re-run on the same day overwrites rather
-- than appending, so the table stays one-row-per-day-per-key and the diff
-- below is always between two days rather than two runs.
CREATE UNIQUE INDEX IF NOT EXISTS surface_records_history_key
    ON public.surface_records_history (snapshot_date, surface, sport,
                                       window_key);

-- The watchdog reads "this key, last two snapshots", so order by key then
-- date descending.
CREATE INDEX IF NOT EXISTS surface_records_history_lookup
    ON public.surface_records_history (surface, sport, window_key,
                                       snapshot_date DESC);

COMMENT ON TABLE public.surface_records_history IS
    'Daily (ET) snapshot of surface_records so day-over-day movement in a '
    'published record is measurable. Added 2026-10-24 after the all-time '
    'Sharp moved +81.21u -> +36.77u overnight with no detector but a human '
    'reading the card. See B172.';

COMMENT ON COLUMN public.surface_records_history.snapshot_date IS
    'ET date, not UTC. A UTC boundary falls at 8pm ET and would split one '
    'slate across two snapshot dates.';

-- Read-only for the app; only the pipeline writes.
GRANT SELECT ON public.surface_records_history TO anon, authenticated;
GRANT ALL    ON public.surface_records_history TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.surface_records_history_id_seq
    TO service_role;

-- PostgREST caches the schema; without this the new table 404s until the
-- next restart (feedback_migration_pgrst_reload).
NOTIFY pgrst, 'reload schema';
