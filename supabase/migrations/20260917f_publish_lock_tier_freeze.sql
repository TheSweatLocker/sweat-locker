-- 2026-09-17f — freeze live tier on locked rows via DB trigger.
-- ==================================================================
-- Andy 9/17 late-PM: publish_lock (20260917e) protects GRADING
-- integrity but the LIVE tier field on mlb_pipeline_props /
-- nfl_pipeline_props still mutates freely — composers reading live
-- tier at regen time can still exclude a locked pick from the surface.
-- Sharp Card regen at 19:52 dropped Lugo because live tier was SKIP
-- (LR override wiped by afternoon generate_props --force), even though
-- Lugo was locked at PRIME 79 in publish_lock.
--
-- Fix: DB trigger blocks tier + conviction UPDATEs on rows whose id
-- has a publish_lock row. Any composer/backfill/pipeline that tries
-- to change tier post-lock silently keeps the locked value. Publisher
-- surfaces regen with the same tier the user saw — no more mid-day
-- disappearances.
--
-- Applies to both mlb_pipeline_props and nfl_pipeline_props. Sport-
-- universal via the same shared publish_lock table.
--
-- Fail-safe: trigger uses SECURITY DEFINER so the publish_lock lookup
-- works from any writer. If publish_lock is missing (defensive), the
-- trigger falls through and allows the update (no-lock = no-protection).
--
-- Composers that write NEW rows (generate_props upsert) are unaffected
-- — insertion isn't gated, only updates that would change tier on
-- already-locked rows. New rows get their tier set at INSERT, THEN
-- get locked by the composer. Order-of-operations preserved.
--
-- ROLLBACK: DROP TRIGGER IF EXISTS <name> ON <table>; DROP FUNCTION.
-- ==================================================================

CREATE OR REPLACE FUNCTION public.enforce_publish_lock_tier() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    lock_row RECORD;
    sport_val TEXT;
BEGIN
    -- Only guard tier / conviction mutations. Other fields (result,
    -- book_line, signals, etc.) can update freely — the lock is
    -- specifically about tier stability.
    IF NEW.tier IS NOT DISTINCT FROM OLD.tier
       AND NEW.conviction IS NOT DISTINCT FROM OLD.conviction THEN
        RETURN NEW;
    END IF;

    -- Derive sport from table name for the lock lookup.
    IF TG_TABLE_NAME = 'mlb_pipeline_props' THEN
        sport_val := 'MLB';
    ELSIF TG_TABLE_NAME = 'nfl_pipeline_props' THEN
        sport_val := 'NFL';
    ELSE
        -- Unknown table — allow update, don't block writers we didn't
        -- explicitly enroll.
        RETURN NEW;
    END IF;

    SELECT tier_at_publish, conviction_at_publish
      INTO lock_row
      FROM public.publish_lock
     WHERE sport = sport_val
       AND market = 'prop'
       AND source_id = OLD.id::text
     LIMIT 1;

    -- No lock present → let the update proceed (unpublished / legacy row).
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    -- Row IS locked — preserve tier + conviction at the locked value.
    -- Other fields on NEW continue to update.
    NEW.tier := lock_row.tier_at_publish;
    NEW.conviction := lock_row.conviction_at_publish;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_publish_lock_tier_mlb ON public.mlb_pipeline_props;
CREATE TRIGGER trg_enforce_publish_lock_tier_mlb
BEFORE UPDATE ON public.mlb_pipeline_props
FOR EACH ROW
EXECUTE FUNCTION public.enforce_publish_lock_tier();

DROP TRIGGER IF EXISTS trg_enforce_publish_lock_tier_nfl ON public.nfl_pipeline_props;
CREATE TRIGGER trg_enforce_publish_lock_tier_nfl
BEFORE UPDATE ON public.nfl_pipeline_props
FOR EACH ROW
EXECUTE FUNCTION public.enforce_publish_lock_tier();

NOTIFY pgrst, 'reload schema';
