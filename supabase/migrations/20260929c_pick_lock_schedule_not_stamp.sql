-- ════════════════════════════════════════════════════════════════════════
-- 20260929c · The pick lock stops locking the whole season on sight
--
-- Andy 2026-09-29: "if the new engine changed we are still early in week we
-- should lock college picks until weekend and ncaaf thursday"
--
-- WHAT WENT WRONG IN 20260926b
-- ----------------------------
-- That migration made the lock a STAMP rather than a schedule, deliberately:
-- "NOT TIME-BASED. Encoding 'Thu 8am ET through Sunday' in SQL duplicates
-- pick_lock.py's schedule in a second place." The reasoning was sound. The
-- consequence was not.
--
-- Two things followed from it:
--
-- 1. The closing backfill stamped EVERY row that had a pick:
--        UPDATE ... SET pick_locked_at = now() WHERE primary_play IS NOT NULL
--    Measured 2026-09-29, all stamped 09-26T16:58:
--        NCAAF    24 of 67 upcoming locked, out to 10-17
--        NFL     208 of 224 upcoming locked, out to 2027-01-10
--    The entire remaining NFL season was frozen at whatever was computed on
--    September 26th — weeks before the data behind those games exists.
--
-- 2. Because the trigger also auto-stamps on first publish, a pick became
--    permanent the instant it was first written, on any day of the week.
--    pick_lock.py's actual cadence is _WEEKLY_LOCK NCAAF (Thu 8am -> Sun) and
--    NFL (Thu 8am -> Mon); lock_active('NCAAF') correctly returned False on
--    this Tuesday while the database refused writes anyway.
--
-- The cost is measurable, not theoretical: pick_lock_drift holds 192 refused
-- changes, ALL from 2026-09-29, and they are the corrections shipped today —
--     kept "Over 58.5"        wanted "Tennessee ML"    (barred-total reroute)
--     kept "Texas Tech -34.5" wanted "Sam Houston +34.5"
-- The lock built to stop impossible states was preserving them.
--
-- THE FIX: the trigger stops deciding WHEN
-- ---------------------------------------
-- 20260926b's objection to putting a schedule in SQL is accepted in full — so
-- no schedule goes in SQL. Instead the trigger gets dumber and the stamp
-- becomes something Python does on purpose:
--
--   * REMOVED: auto-stamp on first publish. An unstamped pick is provisional
--     and freely rewritable, which is what Mon-Wed is for.
--   * KEPT: if pick_locked_at IS NOT NULL, the label is frozen. Unchanged.
--   * ADDED: if the game has already KICKED OFF, the label is frozen whether
--     or not it was ever stamped. This is receipt integrity and it is not a
--     schedule — kickoff_utc is a fact on the row, not a rule that can drift
--     out of sync with pick_lock.py. It also means removing the auto-stamp
--     cannot corrupt a graded receipt even if the locking step never runs.
--
-- Stamping is now lock_football_slate.py, which runs on every football
-- pipeline invocation and stamps the current slate only while
-- pick_lock.lock_active(sport) is true (or kickoff is within 24h, for the
-- Wednesday-night NFL game that the Thursday window would otherwise miss).
-- Running on every invocation rather than one cron means no single firing is
-- a point of failure. The schedule lives in exactly one place, still.
--
-- Net effect: picks float Mon-Wed, firm up Thursday 8am ET, stay put through
-- the weekend, and are immutable once the ball is kicked.
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE OR REPLACE FUNCTION public.enforce_pick_lock() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    sport_val TEXT;
    old_lbl   TEXT;
    new_lbl   TEXT;
    started   BOOLEAN;
BEGIN
    sport_val := CASE TG_TABLE_NAME
                     WHEN 'ncaaf_game_context' THEN 'NCAAF'
                     WHEN 'nfl_game_context'   THEN 'NFL'
                     ELSE upper(TG_TABLE_NAME) END;

    -- No pick yet -> nothing to protect, and NO auto-stamp any more. A first
    -- publish on a Tuesday must stay rewritable until the slate is locked.
    IF OLD.primary_play IS NULL THEN
        RETURN NEW;
    END IF;

    -- Has the ball been kicked? Receipt integrity, independent of any stamp.
    started := (OLD.kickoff_utc IS NOT NULL AND OLD.kickoff_utc <= now());

    -- Not stamped and not started -> provisional, freely writable.
    IF OLD.pick_locked_at IS NULL AND NOT started THEN
        RETURN NEW;
    END IF;

    -- Compare the LABEL, not the whole JSON: sub-text, conviction and audit
    -- notes churn on every rebuild and are not the pick.
    old_lbl := OLD.primary_play->>'label';
    new_lbl := NEW.primary_play->>'label';
    IF new_lbl IS NOT DISTINCT FROM old_lbl THEN
        NEW.pick_locked_at := OLD.pick_locked_at;
        RETURN NEW;
    END IF;

    -- A protected pick is being changed. Refuse it, and say so out loud.
    INSERT INTO public.pick_lock_drift
        (sport, game_id, kept_label, wanted_label, kept, wanted)
    VALUES (sport_val, OLD.game_id, old_lbl, new_lbl,
            OLD.primary_play, NEW.primary_play);

    NEW.primary_play   := OLD.primary_play;
    NEW.pick_locked_at := OLD.pick_locked_at;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.enforce_pick_lock() IS
  'Freezes primary_play.label when pick_locked_at is set OR the game has '
  'kicked off. Does NOT stamp on first publish — lock_football_slate.py does '
  'that while pick_lock.lock_active(sport) is true. See 20260929c.';

-- ── undo the over-stamp ──────────────────────────────────────────────────
-- Release every UPCOMING game so today's recalibrated engine can write. Games
-- that have already kicked off keep their stamp AND are now protected by the
-- started check regardless, so no receipt is touched.
UPDATE public.ncaaf_game_context
   SET pick_locked_at = NULL
 WHERE pick_locked_at IS NOT NULL
   AND (kickoff_utc IS NULL OR kickoff_utc > now());

UPDATE public.nfl_game_context
   SET pick_locked_at = NULL
 WHERE pick_locked_at IS NOT NULL
   AND (kickoff_utc IS NULL OR kickoff_utc > now());

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════
-- VERIFY — expect 0 locked upcoming rows immediately after this runs, then
-- the current slate re-stamped on the first pipeline run after Thu 8am ET.
-- ════════════════════════════════════════════════════════════════════════
-- SELECT 'NCAAF' AS sport,
--        count(*) FILTER (WHERE pick_locked_at IS NOT NULL) AS locked,
--        count(*)                                           AS upcoming
--   FROM public.ncaaf_game_context WHERE kickoff_utc > now()
-- UNION ALL
-- SELECT 'NFL',
--        count(*) FILTER (WHERE pick_locked_at IS NOT NULL),
--        count(*)
--   FROM public.nfl_game_context WHERE kickoff_utc > now();
