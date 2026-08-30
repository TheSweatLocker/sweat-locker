-- Zero-failure team-name pipeline (2026-08-29).
--
-- Adds two things:
--   1. `abbrev` column on ncaaf_team_aliases (was missing — resolver needs
--      it for source that write "FSU"/"TCU"/"USC" style abbrevs).
--   2. team_alias_gaps table — logs any unresolved team-name variant so
--      we get an alert instead of silently dropping picks.
--
-- Second migration to also relax `nickname` NOT NULL if it exists (some
-- FCS teams have no reliable nickname in CFBD).

ALTER TABLE ncaaf_team_aliases
    ADD COLUMN IF NOT EXISTS abbrev TEXT;

CREATE INDEX IF NOT EXISTS idx_ncaaf_aliases_abbrev
    ON ncaaf_team_aliases (abbrev);

-- Gaps table — one row per (sport, source, raw_name) seen; count bumps
-- on repeat sightings. Cron / dashboard reads this to surface aliases
-- that need to be added.
CREATE TABLE IF NOT EXISTS team_alias_gaps (
    id          BIGSERIAL PRIMARY KEY,
    sport       TEXT NOT NULL,
    source      TEXT NOT NULL,
    raw_name    TEXT NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hit_count   INTEGER NOT NULL DEFAULT 1,
    resolved    BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (sport, source, raw_name)
);

CREATE INDEX IF NOT EXISTS idx_team_alias_gaps_unresolved
    ON team_alias_gaps (sport, resolved, last_seen DESC)
    WHERE resolved = FALSE;

NOTIFY pgrst, 'reload schema';
