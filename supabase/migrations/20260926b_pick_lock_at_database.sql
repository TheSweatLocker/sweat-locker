-- 2026-09-26 · The pick stays once it is made. Enforced in ONE place.
--
-- Andy: "THE PICK NEEDS TO STAY ONCE IT IS MADE... is this happening in
-- other sports as well."
--
-- It is. Measured receipt vs live primary_play, game_date >= 09-19:
--
--     NCAAF   141 compared    77 disagree   55%
--     NFL      34 compared    23 disagree   68%
--     MLB     103 compared    31 disagree   30%
--
-- Not all of those are the model changing its mind. Classified:
--
--     different market entirely      59   spread -> ML -> total
--     different pick                 26   genuinely different read
--     OPPOSITE SIDE, same line       25   Michigan -34.5 vs UTEP +34.5
--     same team, market changed      14   ML/spread juice reroute
--     total, line moved               5   benign
--     SAME TEAM, SIGN FLIPPED         2   BAL -8.5 vs BAL +8.5
--
-- The last two categories are impossible states, not decisions — a team
-- cannot be both -8.5 and +8.5.
--
-- WHY A TRIGGER AND NOT MORE PYTHON. pick_lock.py already exists and is
-- correct: lock_active('NCAAF') returned True the whole time Akron's pick
-- was flipping. It was wired into exactly ONE of seven writers. The other
-- six PATCH primary_play directly:
--
--     recompute_ncaaf_primary_play    nfl_ncaaf_signal_discipline
--     ncaaf_fcs_chalk_scorer          ncaaf_model_edge_shadow
--     collapse_sharp_fade_violations  apply_mc_gate_only
--
-- Two are now guarded in Python. Guarding the rest one at a time is the
-- same move that produced this bug, and it loses to the next script
-- somebody writes. The database is the one door every writer must pass.
--
-- NOT TIME-BASED. Encoding "Thu 8am ET through Sunday" in SQL duplicates
-- pick_lock.py's schedule in a second place, and two copies of a rule is
-- two chances to drift — the exact reasoning in pick_lock's own docstring
-- for why MLB and NFL delegate rather than reimplement. Instead the lock
-- is a STAMP: the first time a game gets a non-null primary_play, the
-- trigger records pick_locked_at. From then on the pick is preserved.
-- Literally "stays once it is made", with no schedule to get wrong.
--
-- WHAT STILL UPDATES: everything else on the row. Odds, weather, injuries,
-- scores, tendencies, model fields all refresh normally. Only primary_play
-- freezes. A game with no pick yet is fully writable, because a gap is not
-- a change.
--
-- NOT SILENT. The publish_lock trigger on props (20260917f) preserves the
-- old value and returns 200, which is how 985 discipline demotions were
-- discarded for days with nobody able to see it. This one writes every
-- rejected change to pick_lock_drift, so "what factors wanted to change
-- it" is answerable from a query instead of a guess.
--
-- TO UNLOCK a game deliberately: UPDATE <ctx> SET pick_locked_at = NULL
-- WHERE game_id = '...'; then write. That is an explicit, auditable act.

-- ── stamp column ────────────────────────────────────────────────────
ALTER TABLE public.ncaaf_game_context
    ADD COLUMN IF NOT EXISTS pick_locked_at TIMESTAMPTZ;
ALTER TABLE public.nfl_game_context
    ADD COLUMN IF NOT EXISTS pick_locked_at TIMESTAMPTZ;

-- ── drift log: every refused change, so nothing is silent ───────────
CREATE TABLE IF NOT EXISTS public.pick_lock_drift (
    id           BIGSERIAL PRIMARY KEY,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sport        TEXT NOT NULL,
    game_id      TEXT NOT NULL,
    kept_label   TEXT,
    wanted_label TEXT,
    kept         JSONB,
    wanted       JSONB
);
CREATE INDEX IF NOT EXISTS idx_pick_lock_drift_lookup
    ON public.pick_lock_drift (sport, attempted_at DESC);
ALTER TABLE public.pick_lock_drift ENABLE ROW LEVEL SECURITY;

-- ── the one enforcement point ───────────────────────────────────────
CREATE OR REPLACE FUNCTION public.enforce_pick_lock() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    sport_val TEXT;
    old_lbl   TEXT;
    new_lbl   TEXT;
BEGIN
    sport_val := CASE TG_TABLE_NAME
                     WHEN 'ncaaf_game_context' THEN 'NCAAF'
                     WHEN 'nfl_game_context'   THEN 'NFL'
                     ELSE upper(TG_TABLE_NAME) END;

    -- First publish: stamp it and allow the write.
    IF OLD.primary_play IS NULL AND NEW.primary_play IS NOT NULL THEN
        NEW.pick_locked_at := now();
        RETURN NEW;
    END IF;

    -- No pick, or not yet stamped -> nothing to protect.
    IF OLD.primary_play IS NULL OR OLD.pick_locked_at IS NULL THEN
        RETURN NEW;
    END IF;

    -- Unchanged -> nothing to do. Compare the LABEL, not the whole JSON:
    -- sub-text, conviction and audit notes churn on every rebuild and are
    -- not the pick. Freezing on those would log thousands of non-events.
    old_lbl := OLD.primary_play->>'label';
    new_lbl := NEW.primary_play->>'label';
    IF new_lbl IS NOT DISTINCT FROM old_lbl THEN
        NEW.pick_locked_at := OLD.pick_locked_at;
        RETURN NEW;
    END IF;

    -- A locked pick is being changed. Refuse it, and say so out loud.
    INSERT INTO public.pick_lock_drift
        (sport, game_id, kept_label, wanted_label, kept, wanted)
    VALUES (sport_val, OLD.game_id, old_lbl, new_lbl,
            OLD.primary_play, NEW.primary_play);

    NEW.primary_play   := OLD.primary_play;
    NEW.pick_locked_at := OLD.pick_locked_at;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_pick_lock_ncaaf ON public.ncaaf_game_context;
CREATE TRIGGER trg_enforce_pick_lock_ncaaf
BEFORE UPDATE ON public.ncaaf_game_context
FOR EACH ROW EXECUTE FUNCTION public.enforce_pick_lock();

DROP TRIGGER IF EXISTS trg_enforce_pick_lock_nfl ON public.nfl_game_context;
CREATE TRIGGER trg_enforce_pick_lock_nfl
BEFORE UPDATE ON public.nfl_game_context
FOR EACH ROW EXECUTE FUNCTION public.enforce_pick_lock();

-- Stamp games that already carry a published pick, so today's slate is
-- protected immediately rather than from the next first-publish.
UPDATE public.ncaaf_game_context
   SET pick_locked_at = now()
 WHERE primary_play IS NOT NULL AND pick_locked_at IS NULL;
UPDATE public.nfl_game_context
   SET pick_locked_at = now()
 WHERE primary_play IS NOT NULL AND pick_locked_at IS NULL;

NOTIFY pgrst, 'reload schema';
