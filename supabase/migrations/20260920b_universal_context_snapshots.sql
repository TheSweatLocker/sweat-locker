-- 2026-09-20  game_context_snapshots — one immutable history for every sport
--
-- THE PROBLEM THIS SOLVES
-- mlb_game_context holds 195 rows: 10 in March, 185 in September. April
-- through August is gone. The live context table is a working set, not an
-- archive — it holds roughly the last two weeks.
--
-- Consequence, measured today: asked what the engine thought about a game
-- on 2026-06-20, the answer is nothing. Not the tier, not the conviction,
-- not a single model prediction. The final score survives in
-- mlb_game_results; the reasoning does not.
--
-- That is why there is no self-calibration. It was never a missing
-- feature — you cannot calibrate a model against data that was deleted.
-- Every backtest proposed this week would have hit the same wall; the
-- NCAAF one survives only because ncaaf_game_context goes back to 08-29.
--
-- WHY ONE TABLE AND NOT SIX
-- mlb_game_context_snapshots already exists and captures 24 of 321
-- columns for MLB only. Cloning that per sport means six tables, six
-- field lists, and six chances for a new column to be silently dropped —
-- the exact class of drift that has cost days this week.
--
-- Instead: ONE table, the whole context row preserved as jsonb. It cannot
-- fall out of sync with a source schema, because it does not restate one.
-- A column added to any sport's context table is captured automatically.
--
-- WRITE-ONCE, DELIBERATELY
-- UNIQUE (sport, game_id, snapshot_date) plus ignore-duplicates on the
-- writer means the FIRST capture of a day sticks. The existing MLB
-- snapshotter uses merge-duplicates, so a later run overwrites the
-- morning state — which makes it a record of the last write, not of what
-- was published. A snapshot that can be rewritten is not evidence.
--
-- Nothing is migrated or deleted here. mlb_game_context_snapshots is left
-- untouched; its 5,754 August rows stay exactly where they are.

CREATE TABLE IF NOT EXISTS public.game_context_snapshots (
    id            bigserial PRIMARY KEY,
    sport         text        NOT NULL,
    game_id       text        NOT NULL,
    game_date     date,
    snapshot_date date        NOT NULL,
    captured_at   timestamptz NOT NULL DEFAULT now(),
    -- the entire <sport>_game_context row, verbatim
    context       jsonb       NOT NULL,
    CONSTRAINT game_context_snapshots_uniq
        UNIQUE (sport, game_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS game_context_snapshots_sport_date_idx
    ON public.game_context_snapshots (sport, game_date);
CREATE INDEX IF NOT EXISTS game_context_snapshots_snapdate_idx
    ON public.game_context_snapshots (snapshot_date);
-- Reach into the blob without scanning it: the fields backtests actually
-- filter on are the pick and its tier.
CREATE INDEX IF NOT EXISTS game_context_snapshots_primary_play_idx
    ON public.game_context_snapshots
    USING gin ((context -> 'primary_play'));

COMMENT ON TABLE public.game_context_snapshots IS
    'Immutable daily capture of every sport''s game_context row. Written '
    'once per (sport, game_id, snapshot_date) — the first capture of a '
    'day wins and is never overwritten, so it records what was published '
    'rather than what was last computed. Source of truth for backtesting '
    'and self-calibration; the live *_game_context tables retain only a '
    'working window (~2 weeks for MLB).';
COMMENT ON COLUMN public.game_context_snapshots.context IS
    'Entire source context row as jsonb. Deliberately not restated as '
    'columns so a new field in any sport is captured without a migration.';

NOTIFY pgrst, 'reload schema';
