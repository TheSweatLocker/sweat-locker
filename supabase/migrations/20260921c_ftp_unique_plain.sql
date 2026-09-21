-- 20260921c — fix the fadethepublic_signals upsert key
--
-- 20260921b created the uniqueness index over COALESCE() expressions so that
-- a NULL game_id (an unattributed row) would still collide correctly. That
-- works in Postgres but NOT through PostgREST: `on_conflict=` takes a plain
-- column list and resolves it against a real unique constraint/index, so an
-- expression index is invisible to it. Every write came back
--   42P10: there is no unique or exclusion constraint matching the ON
--          CONFLICT specification
-- and the scraper wrote 0 rows while reporting matches — caught immediately
-- on the first live run.
--
-- The fix is also the more correct key. game_id does not belong in it: it is
-- DERIVED from the fixture, and including a nullable derived column in an
-- identity key is what forced the COALESCE in the first place. What actually
-- identifies a row is the fixture itself — date + sport + market + the two
-- teams — all of which are non-null by construction (the scraper skips any
-- row missing a team, and market is always one of ml/rl/total). So a plain
-- index over those columns is both PostgREST-compatible and a truer key:
-- re-running after the alias table gains a school now UPDATES the existing
-- row with its new game_id instead of inserting a duplicate beside it.

DROP INDEX IF EXISTS ftp_signals_unique;

CREATE UNIQUE INDEX IF NOT EXISTS ftp_signals_unique
    ON fadethepublic_signals (snapshot_date, sport, market, away_team, home_team);

NOTIFY pgrst, 'reload schema';
