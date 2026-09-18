-- 2026-09-18a — public_receipts: immutable log of every user-visible pick.
-- ==================================================================
-- Andy directive 2026-09-18 (2am ET): "each pick needs to be logged and
-- stored somewhere so if anybody ever asks we'll show us the games from
-- where you keep the record ... we need records on records on records."
--
-- Discovered same night: surface_records.prop_prime (Receipts tab
-- source) counts every prop with tier=PRIME regardless of whether it
-- was ever composed onto a user surface. Today's split: 128 tier=PRIME
-- graded, only 6 actually published. App shows "82.8% ROI 47%" — built
-- 95% on picks users never saw. Publicly-posted 97% records built off
-- the same inflated aggregate.
--
-- Fix: universal immutable receipt log. Every composer writes here at
-- publish decision. Aggregators read FROM this table only. Anyone
-- challenges a record on any date → we render the exact list of picks
-- with graded outcomes.
--
-- Design:
--   * UNIQUE (sport, surface, game_date, source_id) — dedupes composer
--     re-runs (idempotent write). First-write wins.
--   * BEFORE UPDATE trigger — only result/actual_value/graded_at may
--     change post-insert. Pick identity (player/line/side/odds/tier/
--     conviction) is FROZEN.
--   * source_table + source_id — audit trail back to originating row.
--   * RLS: public SELECT (transparency), service_role INSERT/UPDATE.
--
-- Backfill: mlb_pipeline/backfill_public_receipts.py reconstructs
-- historical receipts from prop_jerry_reads (9294 MLB rows) + jerry_reads
-- (648) + ledger_snapshots + daily_degen. NON-DESTRUCTIVE — does not
-- touch surface_records; the delta is visible via receipts_report.py.
--
-- ROLLBACK: DROP TABLE public_receipts CASCADE; DROP FUNCTION
-- freeze_receipt_identity CASCADE.
-- ==================================================================

CREATE TABLE IF NOT EXISTS public.public_receipts (
    id                 bigserial PRIMARY KEY,
    sport              text        NOT NULL,     -- MLB / NFL / NCAAF / NBA / NHL / NCAAB / UFC
    surface            text        NOT NULL,     -- sweat_card / sharp_card / prop_jerry / potd / dawg / ladder / ledger / daily_degen
    market             text        NOT NULL,     -- ml / rl / total / prop / parlay
    game_date          date        NOT NULL,
    published_at       timestamptz NOT NULL DEFAULT NOW(),

    -- Pick identity (reconstruction data — enough to render exactly what user saw)
    player_name        text,                      -- NULL for game-side picks
    prop_type          text,                      -- NULL for game-side (e.g. 'receptions_over', 'outs_under')
    pick_side          text,                      -- 'OVER'/'UNDER'/'HOME'/'AWAY'/team abbrev
    pick_line          numeric,                   -- 3.5 receptions, -7 spread, etc.
    pick_odds          integer,                   -- American odds at publish time
    matchup            text,                      -- "DET @ BUF" — human-readable
    pick_label         text,                      -- full display string ("Josh Allen OVER 245.5 pass yds @ -115")

    -- Discipline snapshot
    tier               text,                      -- PRIME / STRONG / LEAN / COVERAGE / SKIP
    conviction         integer,                   -- 0-100 at publish time

    -- Grade (populated post-game; only these three columns are mutable)
    result             text,                      -- WIN / LOSS / PUSH / VOID — NULL until graded
    actual_value       numeric,                   -- 5 receptions, -14 margin, 47 total, etc.
    graded_at          timestamptz,

    -- Provenance / audit trail
    source_table       text        NOT NULL,      -- 'mlb_pipeline_props', 'prop_jerry_reads', 'jerry_reads', etc.
    source_id          text        NOT NULL,      -- id in source table

    -- Free-form audit blob (composer version, gate history, etc.)
    audit              jsonb,

    UNIQUE (sport, surface, game_date, source_id)
);

CREATE INDEX IF NOT EXISTS idx_public_receipts_date_sport
    ON public.public_receipts (game_date DESC, sport);
CREATE INDEX IF NOT EXISTS idx_public_receipts_surface_tier
    ON public.public_receipts (surface, tier);
CREATE INDEX IF NOT EXISTS idx_public_receipts_graded
    ON public.public_receipts (graded_at) WHERE graded_at IS NOT NULL;


-- ─────────────────────────────────────────────────────────────
-- Immutability trigger: identity fields FROZEN after insert.
-- Only result / actual_value / graded_at may change.
-- ─────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.freeze_receipt_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Preserve every identity field. Any attempt to change them is
    -- silently reverted (rewrite NEW → OLD for those columns).
    NEW.sport         := OLD.sport;
    NEW.surface       := OLD.surface;
    NEW.market        := OLD.market;
    NEW.game_date     := OLD.game_date;
    NEW.published_at  := OLD.published_at;
    NEW.player_name   := OLD.player_name;
    NEW.prop_type     := OLD.prop_type;
    NEW.pick_side     := OLD.pick_side;
    NEW.pick_line     := OLD.pick_line;
    NEW.pick_odds     := OLD.pick_odds;
    NEW.matchup       := OLD.matchup;
    NEW.pick_label    := OLD.pick_label;
    NEW.tier          := OLD.tier;
    NEW.conviction    := OLD.conviction;
    NEW.source_table  := OLD.source_table;
    NEW.source_id     := OLD.source_id;
    NEW.audit         := OLD.audit;
    -- result / actual_value / graded_at pass through unchanged (mutable).
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_freeze_receipt_identity ON public.public_receipts;
CREATE TRIGGER trg_freeze_receipt_identity
    BEFORE UPDATE ON public.public_receipts
    FOR EACH ROW EXECUTE FUNCTION public.freeze_receipt_identity();


-- ─────────────────────────────────────────────────────────────
-- RLS: public read (transparency), service_role write.
-- ─────────────────────────────────────────────────────────────

ALTER TABLE public.public_receipts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_receipts_read ON public.public_receipts;
CREATE POLICY public_receipts_read
    ON public.public_receipts
    FOR SELECT
    USING (true);

DROP POLICY IF EXISTS public_receipts_write ON public.public_receipts;
CREATE POLICY public_receipts_write
    ON public.public_receipts
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');


-- ─────────────────────────────────────────────────────────────
-- Report view: side-by-side inflated (surface_records) vs true.
-- Populated by mlb_pipeline/receipts_report.py.
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.receipts_vs_current_records (
    id                bigserial PRIMARY KEY,
    sport             text NOT NULL,
    surface           text NOT NULL,
    window_key        text NOT NULL,       -- d7 / d30 / mtd / lifetime
    tier              text,
    current_wins      int,                 -- from surface_records (what app shows now)
    current_losses    int,
    current_picks     int,
    current_units     numeric,
    receipt_wins      int,                 -- from public_receipts (truth)
    receipt_losses    int,
    receipt_picks     int,
    receipt_units     numeric,
    inflation_ratio   numeric,             -- current_picks / receipt_picks
    computed_at       timestamptz DEFAULT NOW(),
    UNIQUE (sport, surface, window_key, tier)
);

ALTER TABLE public.receipts_vs_current_records ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS receipts_report_read ON public.receipts_vs_current_records;
CREATE POLICY receipts_report_read
    ON public.receipts_vs_current_records
    FOR SELECT USING (true);

DROP POLICY IF EXISTS receipts_report_write ON public.receipts_vs_current_records;
CREATE POLICY receipts_report_write
    ON public.receipts_vs_current_records
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');


NOTIFY pgrst, 'reload schema';
