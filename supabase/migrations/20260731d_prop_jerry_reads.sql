-- 2026-07-31d · Prop Jerry synthesis reads.
--
-- Per-prop 40-60w Jerry take (voice) + parseable directional fields for
-- rendering + grading. Companion to jerry_reads (which is game-level) —
-- this table is prop-level, one row per graded/graded-pending prop pick.
--
-- Same architecture as jerry_reads: sport column so we can extend to
-- NBA/NFL/UFC props once those pipelines produce prop rows.
--
-- Uses composite key (sport, game_id, player_name, prop_type, direction,
-- game_date) mirroring mlb_pipeline_props's natural key so joining is
-- direct.

CREATE TABLE IF NOT EXISTS prop_jerry_reads (
    id               bigserial   PRIMARY KEY,
    sport            text        NOT NULL,
    game_id          text        NOT NULL,
    game_date        date        NOT NULL,
    player_name      text        NOT NULL,
    prop_type        text        NOT NULL,
    direction        text        NOT NULL,           -- 'over' | 'under'

    -- Generation metadata
    generated_at     timestamptz NOT NULL DEFAULT now(),
    prompt_version   text        NOT NULL,

    -- Jerry's outputs
    short_read       text,                            -- 40-60 word take
    call_verdict     text,                            -- 'BACK' | 'FADE' | 'PASS'
    conviction       integer     CHECK (conviction BETWEEN 0 AND 100),

    -- Snapshot for audit
    prop_line        numeric,
    book_odds        integer,
    refit_conviction numeric,
    input_snapshot   jsonb,

    UNIQUE (sport, game_id, player_name, prop_type, direction, game_date)
);
CREATE INDEX IF NOT EXISTS idx_prop_jerry_reads_lookup
    ON prop_jerry_reads (sport, game_date, game_id);

ALTER TABLE prop_jerry_reads DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
