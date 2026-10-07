-- ════════════════════════════════════════════════════════════════════════
-- 2026-10-07 · THE PICK LOCK FREEZES THE PRICE ALONG WITH THE PICK
--
-- Andy: "I need the line movement to assessed and not stale lines post as
-- picks thats what i need just fix the systems."
--
-- WHAT WENT WRONG
-- `enforce_pick_lock` compares `primary_play->>'label'`. That label bundles
-- the team WITH the number — "BAL -6" — so a line refresh is byte-for-byte
-- indistinguishable from someone flipping the pick to a different team. Once
-- Thursday stamps `pick_locked_at`, the NUMBER is frozen for the week too.
--
-- BAL @ ATL opened BAL -6.5, traded -6.0 on 139 captures, and is now BAL
-- +3.5 — a 9.5-point swing with the favourite flipping, almost certainly
-- injury news. The card said "BAL -6" all week. Every week-5 game carried a
-- stamp (2026-10-02..10-07), so when the label normalizer ran today it was
-- refused on 10 of 11 games. The only one that landed, BUF @ LA, is the only
-- one with no stamp.
--
-- This contradicts the lock's own stated intent. 20260926b's comment says the
-- lock exists so "mid-week ensemble drift" cannot flip "a pick out from under
-- a frozen jerry writeup", and recompute_nfl_primary_play's labels-only pass
-- says in as many words that it "changes NONE of those: it only rewrites
-- label and line to agree with the side the lock already froze". Both are
-- right about the intent. The trigger just could not see the difference,
-- because it was handed one string with two facts in it.
--
-- WHAT CHANGES
-- Compare the two fields that actually identify a pick:
--     side   HOME / AWAY / OVER / UNDER
--     type   ml / rl / spread / total
-- plus the TEAM NAME at the head of the label, which is what a reader
-- recognises and what a grade resolves against.
--
-- A change to any of those is a different bet and is still refused.
-- A change to the NUMBER ALONE is the market moving, and is now allowed.
--
-- STILL ABSOLUTELY FROZEN once the ball is kicked. `started` is checked
-- first and refuses everything, number included, because re-deriving a
-- played game's price would restate what we claim to have taken. That is
-- the receipt-integrity half of this trigger and it is untouched.
--
-- RETRACTED from the first version of this migration: I wrote that
-- `pick_lock_drift` "does not exist (PostgREST returns 400 on it)". It
-- exists and it works — it had already logged all 20 refusals from today's
-- two runs. The 400 was MY query selecting a `created_at` column; the real
-- timestamp column is `attempted_at`. PostgREST returns 400 for an unknown
-- COLUMN exactly as it does for an unknown table, and I read one as the
-- other. That first version then failed to apply (42703) because
-- CREATE TABLE IF NOT EXISTS silently skipped the existing table and the
-- index on the non-existent column was what actually errored.
--
-- So the only thing the log needs is a `reason`, added below. Lesson worth
-- keeping: a 400 names a column as readily as a table — SELECT * before
-- concluding something is missing.
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

-- The table already exists with: id, sport, game_id, kept_label,
-- wanted_label, kept, wanted, attempted_at. Only `reason` is new, so this is
-- an ALTER and not a CREATE — and every statement is written to be safe on
-- both an existing and a fresh database.
CREATE TABLE IF NOT EXISTS public.pick_lock_drift (
    id            BIGSERIAL PRIMARY KEY,
    sport         TEXT,
    game_id       TEXT,
    kept_label    TEXT,
    wanted_label  TEXT,
    kept          JSONB,
    wanted        JSONB,
    attempted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.pick_lock_drift
    ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE public.pick_lock_drift
    ADD COLUMN IF NOT EXISTS attempted_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS pick_lock_drift_attempted_idx
    ON public.pick_lock_drift (attempted_at DESC);
CREATE INDEX IF NOT EXISTS pick_lock_drift_game_idx
    ON public.pick_lock_drift (sport, game_id);

COMMENT ON TABLE public.pick_lock_drift IS
  'Every primary_play write the pick lock refused. Written by '
  'enforce_pick_lock(). A row here means a script believed it had saved a '
  'pick and had not — check it before trusting any "repaired N" count.';


CREATE OR REPLACE FUNCTION public.enforce_pick_lock() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    sport_val TEXT;
    old_lbl   TEXT;
    new_lbl   TEXT;
    old_team  TEXT;
    new_team  TEXT;
    started   BOOLEAN;
BEGIN
    sport_val := CASE TG_TABLE_NAME
                     WHEN 'ncaaf_game_context' THEN 'NCAAF'
                     WHEN 'nfl_game_context'   THEN 'NFL'
                     ELSE upper(TG_TABLE_NAME) END;

    -- No pick yet -> nothing to protect.
    IF OLD.primary_play IS NULL THEN
        RETURN NEW;
    END IF;

    started := (OLD.kickoff_utc IS NOT NULL AND OLD.kickoff_utc <= now());

    -- Not stamped and not started -> provisional, freely writable.
    IF OLD.pick_locked_at IS NULL AND NOT started THEN
        RETURN NEW;
    END IF;

    old_lbl := OLD.primary_play->>'label';
    new_lbl := NEW.primary_play->>'label';

    -- Identical label: nothing to decide. Sub-text, conviction and audit
    -- notes churn on every rebuild and are not the pick.
    IF new_lbl IS NOT DISTINCT FROM old_lbl THEN
        NEW.pick_locked_at := OLD.pick_locked_at;
        RETURN NEW;
    END IF;

    -- ── KICKED OFF: freeze everything, number included ──────────────────
    -- A settled price must never be restated. This is the receipt-integrity
    -- half of the trigger and it is deliberately stricter than the rest.
    IF started THEN
        INSERT INTO public.pick_lock_drift
            (sport, game_id, kept_label, wanted_label, kept, wanted, reason)
        VALUES (sport_val, OLD.game_id, old_lbl, new_lbl,
                OLD.primary_play, NEW.primary_play,
                'game already started - price is frozen');
        NEW.primary_play   := OLD.primary_play;
        NEW.pick_locked_at := OLD.pick_locked_at;
        RETURN NEW;
    END IF;

    -- ── STAMPED BUT NOT STARTED: the pick is frozen, the PRICE is not ───
    -- A pick is identified by its side, its market, and the team named at
    -- the head of the label. The number after the team is the market's, and
    -- the market is allowed to move.
    --
    -- split_part on space is deliberate and sufficient: every label in both
    -- tables is "<team> <number>", "<team> ML", or "Over/Under <number>".
    -- College team names contain spaces ("Penn State -7"), so compare
    -- everything up to the LAST space rather than the first token — taking
    -- the first token would read "Penn State" and "Penn Quakers" as the same
    -- team, which is the same class of error as the `team.split()[-1]`
    -- college-name bug already fixed in defensive_gates.
    old_team := NULLIF(regexp_replace(old_lbl, '\s+\S+$', ''), '');
    new_team := NULLIF(regexp_replace(new_lbl, '\s+\S+$', ''), '');

    IF  (NEW.primary_play->>'side') IS NOT DISTINCT FROM (OLD.primary_play->>'side')
    AND (NEW.primary_play->>'type') IS NOT DISTINCT FROM (OLD.primary_play->>'type')
    AND new_team IS NOT DISTINCT FROM old_team
    THEN
        -- Same bet, new number. Let it through and keep the stamp.
        NEW.pick_locked_at := OLD.pick_locked_at;
        RETURN NEW;
    END IF;

    -- Side, market or team changed -> a different pick. Refuse, and say so.
    INSERT INTO public.pick_lock_drift
        (sport, game_id, kept_label, wanted_label, kept, wanted, reason)
    VALUES (sport_val, OLD.game_id, old_lbl, new_lbl,
            OLD.primary_play, NEW.primary_play,
            format('locked pick changed: side %s->%s type %s->%s team %s->%s',
                   OLD.primary_play->>'side', NEW.primary_play->>'side',
                   OLD.primary_play->>'type', NEW.primary_play->>'type',
                   old_team, new_team));

    NEW.primary_play   := OLD.primary_play;
    NEW.pick_locked_at := OLD.pick_locked_at;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.enforce_pick_lock() IS
  'Freezes a locked pick''s SIDE, MARKET and TEAM while allowing the LINE to '
  'track the market, so a stale number is never published as a pick. Freezes '
  'everything including the number once kickoff_utc has passed. Refusals are '
  'logged to pick_lock_drift. See 20261007a.';

COMMIT;

-- PostgREST caches the schema; without this the new table 404s.
NOTIFY pgrst, 'reload schema';
