-- 20261028c · I LEFT THE PRIMARY KEY THAT WAS THE WHOLE PROBLEM
--
-- My own mistake in 20261028a. That migration added
--     CREATE UNIQUE INDEX daily_dawg_sport_date_uniq ON daily_dawg (sport, game_date)
-- and said, in its own comment:
--
--     "Done as a UNIQUE INDEX rather than a new primary key so the existing
--      PK and every FK that may reference it keep working"
--
-- The existing PK is `daily_dawg_pkey` on (game_date) ALONE. That is the
-- exact constraint that makes a multi-sport Dawg impossible, and I reasoned
-- about protecting it instead of noticing it was the thing I was removing.
-- Proven immediately on the first real write:
--
--     MLB  Tampa Bay Rays ML  +135   -> wrote
--     NHL  Seattle Kraken ML  +117   -> 23505 duplicate key value violates
--                                      unique constraint "daily_dawg_pkey"
--                                      Key (game_date)=(2026-10-28) already exists
--
-- One Dawg per DAY is what the table enforces; one Dawg per SPORT per day is
-- what Andy asked for and what 20261028a's index describes.
--
-- SAFETY. If any foreign key references daily_dawg(game_date), this DROP
-- fails and the transaction rolls back -- loudly, changing nothing. That is
-- the desired behaviour: a silent success that broke a reference would be far
-- worse. Nothing in mlb_pipeline joins to daily_dawg by a FK (the surface is
-- read by game_date and by sport), so this is expected to apply cleanly.
--
-- The (sport, game_date) unique index from 20261028a becomes the real key and
-- is promoted to the primary key, so the table keeps one.

BEGIN;

ALTER TABLE public.daily_dawg
    DROP CONSTRAINT IF EXISTS daily_dawg_pkey;

-- Both columns must be NOT NULL to carry a primary key. sport was backfilled
-- to 'MLB' on every existing row by 20261028a.
ALTER TABLE public.daily_dawg
    ALTER COLUMN sport SET NOT NULL;

ALTER TABLE public.daily_dawg
    ADD CONSTRAINT daily_dawg_pkey PRIMARY KEY USING INDEX daily_dawg_sport_date_uniq;

COMMENT ON TABLE public.daily_dawg IS
  'Dawg of the Day. Keyed on (sport, game_date) since 20261028c - it was '
  'keyed on game_date alone, which capped the surface at one pick per day '
  'across all sports and was the reason it could not go multi-sport.';

NOTIFY pgrst, 'reload schema';

COMMIT;
