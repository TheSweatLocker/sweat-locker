-- 2026-07-31 · Jerry-as-synthesizer product direction (Tier 2)
--
-- Two new tables anchor the shift from "many chips" to "one Jerry read":
--   1. jerry_reads: pre-generated analytical reads (short + long) per game.
--      Grades post-game; feeds POTD + card + detail. First jerry_reads row
--      lands 2026-07-31; historical Jerry writeups from generate_mlb_game_reads
--      live in a separate cache (jerry_cache) and are unaffected.
--   2. external_source_track_record: nightly aggregate of source × sport ×
--      surface × 30d/90d hit rate. Jerry cites source W/L when weighing
--      external picks in his synthesis.

-- ─── jerry_reads ─────────────────────────────────────────────────────────
-- One row per (sport, game_id, game_date). Regenerable in-place — upsert
-- semantics on the unique constraint.
CREATE TABLE IF NOT EXISTS jerry_reads (
    id               bigserial   PRIMARY KEY,
    sport            text        NOT NULL,
    game_id          text        NOT NULL,
    game_date        date        NOT NULL,

    -- Generation metadata
    generated_at     timestamptz NOT NULL DEFAULT now(),
    prompt_version   text        NOT NULL,          -- e.g. 'synthesis_v1' for future prompt swaps
    input_snapshot   jsonb,                          -- exact struct Jerry saw (audit trail)

    -- Jerry's outputs
    call_text        text,                           -- machine-parseable directional call ("PIT ML" / "Under 8.5" / "PASS")
    call_market      text,                           -- 'ml' | 'rl' | 'total' | 'prop' | 'pass' | 'other'
    call_side        text,                           -- 'HOME' | 'AWAY' | 'OVER' | 'UNDER' | null
    call_line        numeric,
    call_odds_est    integer,                        -- Jerry's implied odds if he cited them
    conviction       integer     CHECK (conviction BETWEEN 0 AND 100),  -- 0-100
    short_read       text,                           -- 40-60 word card preview
    long_read        text,                           -- 200-300 word GameDetailV2 body

    -- Grading (set post-game by grade_jerry_reads.py)
    resolved_at      timestamptz,
    result           text,                           -- 'W' | 'L' | 'P' | 'VOID' | 'NO_ACTION'
    actual_outcome   jsonb,                          -- final score + market outcomes for audit

    UNIQUE (sport, game_id, game_date)
);
CREATE INDEX IF NOT EXISTS idx_jerry_reads_lookup   ON jerry_reads (sport, game_date, game_id);
CREATE INDEX IF NOT EXISTS idx_jerry_reads_potd     ON jerry_reads (game_date DESC, conviction DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_jerry_reads_result   ON jerry_reads (game_date DESC, result) WHERE result IS NOT NULL;


-- ─── external_source_track_record ───────────────────────────────────────
-- Nightly job aggregates external_picks.result by (source, sport, surface)
-- over rolling 30d and 90d windows. Jerry pulls this to weight source
-- opinions in his synthesis ("source X is 4-11 on ML last 30d → discount").
CREATE TABLE IF NOT EXISTS external_source_track_record (
    id               bigserial   PRIMARY KEY,
    computed_at      timestamptz NOT NULL DEFAULT now(),
    source           text        NOT NULL,
    sport            text        NOT NULL,
    surface          text        NOT NULL,           -- 'ml' | 'rl' | 'total' | 'prop' | 'overall'

    window_days      integer     NOT NULL,           -- 30 | 90 (compute both nightly)
    n_picks          integer     NOT NULL DEFAULT 0,
    n_wins           integer     NOT NULL DEFAULT 0,
    n_losses         integer     NOT NULL DEFAULT 0,
    n_pushes         integer     NOT NULL DEFAULT 0,
    hit_rate         numeric,                        -- wins / (wins + losses) — nullable when denom = 0
    roi              numeric,                        -- if we have odds, ROI at flat $100/pick; nullable
    UNIQUE (source, sport, surface, window_days)
);
CREATE INDEX IF NOT EXISTS idx_ext_track_lookup ON external_source_track_record (source, sport, surface, window_days);


-- ─── Aggregate view: Jerry record for display ───────────────────────────
-- Marketing headline: "Jerry is 68-42 last 30d." This view is what the
-- app hits — one query per screen instead of a scan.
CREATE OR REPLACE VIEW jerry_record_summary AS
SELECT
    sport,
    call_market,
    COUNT(*) FILTER (WHERE result = 'W')                                        AS wins,
    COUNT(*) FILTER (WHERE result = 'L')                                        AS losses,
    COUNT(*) FILTER (WHERE result = 'P')                                        AS pushes,
    COUNT(*) FILTER (WHERE result IN ('W','L'))                                 AS graded,
    ROUND(
        (COUNT(*) FILTER (WHERE result = 'W')::numeric
         / NULLIF(COUNT(*) FILTER (WHERE result IN ('W','L')), 0)) * 100, 1
    )                                                                            AS hit_rate_pct,
    ROUND(
        (COUNT(*) FILTER (WHERE result = 'W' AND game_date >= now()::date - 30)::numeric
         / NULLIF(COUNT(*) FILTER (WHERE result IN ('W','L') AND game_date >= now()::date - 30), 0)) * 100, 1
    )                                                                            AS hit_rate_pct_30d
FROM jerry_reads
WHERE result IS NOT NULL AND call_market != 'pass'
GROUP BY sport, call_market;

NOTIFY pgrst, 'reload schema';
