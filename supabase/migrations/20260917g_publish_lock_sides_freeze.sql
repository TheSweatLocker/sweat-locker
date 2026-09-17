-- 2026-09-17g — sides extension: freeze primary_play tier via trigger.
-- ==================================================================
-- Andy 9/17 late-PM: publish_lock (20260917e) covers PROP tier freeze
-- via 20260917f trigger on {mlb,nfl}_pipeline_props. Same yo-yo class
-- affects SIDES (ml / rl / total picks on <sport>_game_context.
-- primary_play JSONB) across every sport. Ensemble scorer sets tier,
-- recompute_*_primary_play + defensive_gates + calibration can flip
-- tier mid-day. Publish_lock schema already supports (sport, market,
-- source_id=game_id) but no trigger to enforce it.
--
-- Fix: trigger on each <sport>_game_context table that, on UPDATE,
-- checks publish_lock for a matching (sport, market, source_id) row.
-- If found, rewrites NEW.primary_play->>'tier' + 'conviction' back
-- to the locked values via jsonb_set. Composer regens see the same
-- tier the user saw — no mid-day disappearances on sides either.
--
-- Sports enrolled: MLB / NFL / NCAAF / NBA / NHL / NCAAB / UFC.
-- Uses primary_play->>'type' to derive market (ml/rl/total). If
-- primary_play or primary_play->>'type' is missing, trigger falls
-- through (no lock check, allow update).
--
-- ROLLBACK: DROP TRIGGER ... ON each table; DROP FUNCTION.
-- ==================================================================

CREATE OR REPLACE FUNCTION public.enforce_publish_lock_side() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    lock_row RECORD;
    sport_val TEXT;
    market_val TEXT;
    old_tier TEXT;
    new_tier TEXT;
    old_conv INTEGER;
    new_conv INTEGER;
BEGIN
    -- Bail if primary_play didn't change, or NEW.primary_play is null.
    IF NEW.primary_play IS NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.primary_play IS NOT DISTINCT FROM OLD.primary_play THEN
        RETURN NEW;
    END IF;

    -- Only guard tier / conviction mutations inside primary_play.
    old_tier := COALESCE(OLD.primary_play->>'tier', '');
    new_tier := COALESCE(NEW.primary_play->>'tier', '');
    old_conv := NULLIF(OLD.primary_play->>'conviction', '')::int;
    new_conv := NULLIF(NEW.primary_play->>'conviction', '')::int;
    IF old_tier = new_tier AND old_conv IS NOT DISTINCT FROM new_conv THEN
        RETURN NEW;
    END IF;

    -- Derive sport from table name for lock lookup.
    sport_val := CASE TG_TABLE_NAME
        WHEN 'mlb_game_context'   THEN 'MLB'
        WHEN 'nfl_game_context'   THEN 'NFL'
        WHEN 'ncaaf_game_context' THEN 'NCAAF'
        WHEN 'nba_game_context'   THEN 'NBA'
        WHEN 'nhl_game_context'   THEN 'NHL'
        WHEN 'ncaab_game_context' THEN 'NCAAB'
        WHEN 'ufc_game_context'   THEN 'UFC'
        ELSE NULL
    END;
    IF sport_val IS NULL THEN
        RETURN NEW;
    END IF;

    -- Market from primary_play.type — 'ml' / 'rl' / 'total'.
    market_val := LOWER(COALESCE(NEW.primary_play->>'type', ''));
    IF market_val NOT IN ('ml', 'rl', 'total') THEN
        RETURN NEW;
    END IF;

    SELECT tier_at_publish, conviction_at_publish
      INTO lock_row
      FROM public.publish_lock
     WHERE sport = sport_val
       AND market = market_val
       AND source_id = NEW.game_id
     LIMIT 1;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    -- Row IS locked — rewrite tier + conviction inside primary_play.
    -- Preserve everything else via jsonb_set chain.
    NEW.primary_play := jsonb_set(
        NEW.primary_play,
        '{tier}',
        to_jsonb(lock_row.tier_at_publish),
        true
    );
    IF lock_row.conviction_at_publish IS NOT NULL THEN
        NEW.primary_play := jsonb_set(
            NEW.primary_play,
            '{conviction}',
            to_jsonb(lock_row.conviction_at_publish),
            true
        );
    END IF;
    RETURN NEW;
END;
$$;

-- Enroll every sport's game_context table.
DROP TRIGGER IF EXISTS trg_enforce_publish_lock_side_mlb ON public.mlb_game_context;
CREATE TRIGGER trg_enforce_publish_lock_side_mlb
BEFORE UPDATE ON public.mlb_game_context
FOR EACH ROW
EXECUTE FUNCTION public.enforce_publish_lock_side();

DROP TRIGGER IF EXISTS trg_enforce_publish_lock_side_nfl ON public.nfl_game_context;
CREATE TRIGGER trg_enforce_publish_lock_side_nfl
BEFORE UPDATE ON public.nfl_game_context
FOR EACH ROW
EXECUTE FUNCTION public.enforce_publish_lock_side();

DROP TRIGGER IF EXISTS trg_enforce_publish_lock_side_ncaaf ON public.ncaaf_game_context;
CREATE TRIGGER trg_enforce_publish_lock_side_ncaaf
BEFORE UPDATE ON public.ncaaf_game_context
FOR EACH ROW
EXECUTE FUNCTION public.enforce_publish_lock_side();

-- NBA / NHL / NCAAB / UFC — enrolled defensively for launch season.
-- IF NOT EXISTS pattern lets the trigger fail silently if the table
-- doesn't exist yet (pre-launch season). Each sport re-runs the
-- CREATE TRIGGER on their launch day.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'nba_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_enforce_publish_lock_side_nba ON public.nba_game_context';
        EXECUTE 'CREATE TRIGGER trg_enforce_publish_lock_side_nba
                 BEFORE UPDATE ON public.nba_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.enforce_publish_lock_side()';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'nhl_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_enforce_publish_lock_side_nhl ON public.nhl_game_context';
        EXECUTE 'CREATE TRIGGER trg_enforce_publish_lock_side_nhl
                 BEFORE UPDATE ON public.nhl_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.enforce_publish_lock_side()';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'ncaab_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_enforce_publish_lock_side_ncaab ON public.ncaab_game_context';
        EXECUTE 'CREATE TRIGGER trg_enforce_publish_lock_side_ncaab
                 BEFORE UPDATE ON public.ncaab_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.enforce_publish_lock_side()';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'ufc_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_enforce_publish_lock_side_ufc ON public.ufc_game_context';
        EXECUTE 'CREATE TRIGGER trg_enforce_publish_lock_side_ufc
                 BEFORE UPDATE ON public.ufc_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.enforce_publish_lock_side()';
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
