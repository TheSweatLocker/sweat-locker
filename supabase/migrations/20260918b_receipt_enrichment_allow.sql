-- 2026-09-18b — allow one-time enrichment on public_receipts.
-- ==================================================================
-- The freeze_receipt_identity trigger blocks ALL updates to identity
-- fields. But we need to backfill tier/conviction/pick_odds from
-- mlb_pipeline_props (prop_jerry_reads doesn't carry these; they only
-- exist in the source prop row).
--
-- Change: allow NULL → value updates on tier / conviction / pick_odds
-- / matchup. Once set to a non-NULL value, they FREEZE (same
-- immutability guarantee, just deferred to first assignment).
-- Identity fields (player_name/prop_type/pick_line/etc.) still fully
-- frozen — they cannot be null-filled because they're required at
-- insert.
--
-- ROLLBACK: DROP FUNCTION would restore the strict trigger.
-- ==================================================================

CREATE OR REPLACE FUNCTION public.freeze_receipt_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- FULLY frozen (required on insert, cannot change):
    NEW.sport         := OLD.sport;
    NEW.surface       := OLD.surface;
    NEW.market        := OLD.market;
    NEW.game_date     := OLD.game_date;
    NEW.published_at  := OLD.published_at;
    NEW.player_name   := OLD.player_name;
    NEW.prop_type     := OLD.prop_type;
    NEW.pick_side     := OLD.pick_side;
    NEW.pick_line     := OLD.pick_line;
    NEW.pick_label    := OLD.pick_label;
    NEW.source_table  := OLD.source_table;
    NEW.source_id     := OLD.source_id;
    -- Set-once (NULL → value allowed; value → any other value blocked):
    IF OLD.tier IS NOT NULL THEN
        NEW.tier := OLD.tier;
    END IF;
    IF OLD.conviction IS NOT NULL THEN
        NEW.conviction := OLD.conviction;
    END IF;
    IF OLD.pick_odds IS NOT NULL THEN
        NEW.pick_odds := OLD.pick_odds;
    END IF;
    IF OLD.matchup IS NOT NULL THEN
        NEW.matchup := OLD.matchup;
    END IF;
    IF OLD.audit IS NOT NULL THEN
        NEW.audit := OLD.audit;
    END IF;
    -- Fully mutable (grading fills these in post-game):
    -- result, actual_value, graded_at
    RETURN NEW;
END;
$$;

NOTIFY pgrst, 'reload schema';
