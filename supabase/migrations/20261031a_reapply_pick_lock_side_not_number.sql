-- ════════════════════════════════════════════════════════════════════════
-- 2026-10-31 · RE-APPLY: the pick lock is freezing the NUMBER again
--
-- 20261007a was applied on 10-07 and worked. pick_lock_drift proves it:
-- every insert in that version writes a non-null `reason`, and reasons
-- exist ONLY between 2026-10-07T22:52 and 2026-10-08T19:03. All 4,164
-- refusals since carry reason = NULL, including today's. So the function
-- body was replaced on 10-08 and the old one is live again.
--
-- Cost so far: 799 refusals where the side, the market and the team were
-- ALL unchanged -- pure line refreshes the trigger ate. NFL 497,
-- NCAAF 302. PIT -3 -> PIT -5.5, CHI -1.5 -> CHI -4.5,
-- SF -2.5 -> SF +5.5.
--
-- This file is ONLY the function. 20261007a's table, column and index
-- statements already landed (pick_lock_drift.reason exists and returns 200),
-- so re-running them is unnecessary.
--
-- NO BEGIN/COMMIT, deliberately: a trailing failure must not roll back good
-- work (feedback_notify_pgrst_breaks_andys_sql_client). It is a single
-- statement, so there is nothing to make atomic.
--
-- NO `NOTIFY pgrst` either. It is what makes the SQL client report
-- `42P01 relation "a" does not exist` whether the migration failed OR fully
-- succeeded, and it is not needed here: nothing new is exposed over the API,
-- only an existing trigger function's body changes.
--
-- VERIFY BY ARTEFACT, not by the client's message:
--     python mlb_pipeline/check_pick_lock_version.py
-- A non-null `reason` on the next refusal is the proof.
-- ════════════════════════════════════════════════════════════════════════

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
