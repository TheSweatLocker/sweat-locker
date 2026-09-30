-- ════════════════════════════════════════════════════════════════════════
-- 20260929d · The pick lock was un-unlockable. Fixing my own 20260929c.
--
-- 20260929c shipped a fix for the over-stamped season and its unlock UPDATE
-- DID NOT APPLY. Verified after the migration was applied:
--
--     NCAAF   24 of 67 upcoming still locked
--     NFL    208 of 224 upcoming still locked, out to 2027-01-10
--
-- Reproduced on a single row, which is the whole story:
--
--     PATCH nfl_game_context?game_id=eq.97cbd9ad… {"pick_locked_at": null}
--       -> 204 No Content
--     SELECT pick_locked_at -> 2026-09-26T16:58:53   (unchanged)
--
-- CAUSE — one line, present in 20260926b's trigger AND carried into mine:
--
--     IF new_lbl IS NOT DISTINCT FROM old_lbl THEN
--         NEW.pick_locked_at := OLD.pick_locked_at;   <-- here
--         RETURN NEW;
--     END IF;
--
-- Clearing the stamp never changes primary_play, so the label always compares
-- equal, so every attempt lands in that branch and has the stamp written
-- straight back. The column was effectively read-only.
--
-- This also means 20260926b's own documented escape hatch never worked:
--     "TO UNLOCK a game deliberately: UPDATE <ctx> SET pick_locked_at = NULL
--      WHERE game_id = '...'; then write. That is an explicit, auditable act."
-- It was neither explicit nor auditable, because it silently did nothing and
-- returned success. Same silent-success class as the publish_lock trigger that
-- 20260926b was itself written to avoid, and the reason I only caught it was
-- reading the lock counts back instead of trusting the 204.
--
-- THE FIX
-- -------
-- Drop the forced restore. It was only ever defensive: in an UPDATE, NEW
-- already carries OLD's value for every column the statement does not SET, and
-- PostgREST's merge-duplicates upsert emits DO UPDATE SET for provided columns
-- only — so a routine ctx rebuild that never mentions pick_locked_at cannot
-- clear it. The line bought nothing and cost the ability to unlock.
--
-- Deliberate changes to the stamp are now honoured AND logged to
-- pick_lock_drift, so "who cleared this lock and when" is a query rather than a
-- guess. That is what 20260926b meant by auditable.
--
-- The label protection itself is untouched: a stamped or kicked-off game still
-- refuses a label change and still logs it.
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

    -- Log a deliberate stamp change (set or cleared) so it is auditable. Done
    -- before any early return, because unlocking is exactly the act that most
    -- needs a record.
    IF NEW.pick_locked_at IS DISTINCT FROM OLD.pick_locked_at THEN
        INSERT INTO public.pick_lock_drift
            (sport, game_id, kept_label, wanted_label, kept, wanted)
        VALUES (sport_val, OLD.game_id,
                CASE WHEN OLD.pick_locked_at IS NULL THEN 'unstamped'
                     ELSE 'stamped ' || OLD.pick_locked_at::text END,
                CASE WHEN NEW.pick_locked_at IS NULL THEN 'UNLOCK'
                     ELSE 'LOCK ' || NEW.pick_locked_at::text END,
                OLD.primary_play, NEW.primary_play);
    END IF;

    -- No pick yet -> nothing to protect. No auto-stamp (20260929c).
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
        -- NOTE: deliberately does NOT restore OLD.pick_locked_at. That line is
        -- what made the stamp unclearable. See the header.
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
  'Freezes primary_play.label when pick_locked_at is set OR the game has kicked '
  'off. Does NOT stamp on first publish (lock_football_slate.py does that while '
  'pick_lock.lock_active is true). pick_locked_at is deliberately writable so a '
  'slate can be released, and every change to it is logged to pick_lock_drift. '
  'See 20260929c + 20260929d.';

-- ── now the release actually applies ─────────────────────────────────────
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
-- VERIFY — do NOT trust the 204 this time. Read the counts back; both `locked`
-- columns must be 0. Started games keep their stamp and are protected by the
-- kicked-off check regardless.
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
