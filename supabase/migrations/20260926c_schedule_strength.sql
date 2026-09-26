-- 2026-09-26 · Strength of Schedule + Strength of Record, every sport.
--
-- Andy: "SOS and SOR added somewhere in game detail across all sports,
-- will def help in college sports modeling with the red green
-- highlighting which is better."
--
-- THE POINT OF DOING IT THIS WAY: the Team Stats card is already generic
-- over stat_key — it reads team_stats_rolling and renders whatever rows
-- it finds, with the percentile chip, the head-to-head red/green and the
-- ⓘ. So adding two stat_keys there means SOS and SOR arrive with all of
-- that for free: no new component, no second fetch, no extra workflow
-- step to keep in sync. Andy, same day: "these need to be injected in
-- current processes not adding workflow to confuse system."
--
-- WHY A SEPARATE TABLE UNIONED IN, rather than extending the matview:
-- team_stats_rolling_full is a MATERIALIZED VIEW rebuilt from source on
-- every refresh, so anything computed in Python cannot live in it — the
-- next REFRESH would drop it. Same trap that bit team_situational_records
-- on 20260916a. A real table survives; the view stitches the two together
-- so the client still sees one source.
--
-- The pre-2026 football filter from 20260916a is carried forward verbatim
-- on the matview side. Per feedback_publishable_view_drift, a
-- CREATE OR REPLACE VIEW replaces the WHOLE definition, so an existing
-- rule that is not restated is silently deleted.
--
-- SOS vs SOR — they are different questions and both are wanted:
--   SOS  how hard the opponents you played are. Says nothing about you;
--        a 1-4 team can lead the country in it.
--   SOR  how impressive your record is GIVEN that schedule:
--            your win% − (win% an average team would expect vs that slate)
--        +0.30 means you win 30 points more often than a neutral team
--        would against the same opponents.
--
-- Head-to-head is excluded when rating an opponent — otherwise every team
-- that beats you inflates your SOS because its record includes that win.
-- Measured before the correction: a 0-2 Charlotte showed SOS 1.000.
-- See compute_schedule_strength.py.

CREATE TABLE IF NOT EXISTS public.team_computed_stats (
    sport         TEXT    NOT NULL,
    team          TEXT    NOT NULL,
    season        INTEGER NOT NULL,
    stat_key      TEXT    NOT NULL,
    raw_value     NUMERIC,
    rank          INTEGER,
    league_size   INTEGER,
    direction     TEXT,
    display_label TEXT,
    unit          TEXT DEFAULT '',
    refreshed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT team_computed_stats_pk
        PRIMARY KEY (sport, team, season, stat_key)
);

CREATE INDEX IF NOT EXISTS idx_team_computed_stats_lookup
    ON public.team_computed_stats (sport, team, season);

ALTER TABLE public.team_computed_stats ENABLE ROW LEVEL SECURITY;

-- The app reads this view anonymously, so the computed half needs a read
-- policy of its own — the matview half is already readable.
DROP POLICY IF EXISTS team_computed_stats_read ON public.team_computed_stats;
CREATE POLICY team_computed_stats_read
    ON public.team_computed_stats FOR SELECT
    USING (true);

CREATE OR REPLACE VIEW public.team_stats_rolling AS
SELECT sport, team, season, stat_key, raw_value, rank, league_size,
       direction, display_label, unit, refreshed_at
  FROM public.team_stats_rolling_full
 -- Rule carried from 20260916a: football is current-season only.
 WHERE NOT (sport IN ('NFL', 'NCAAF') AND season < 2026)
UNION ALL
SELECT sport, team, season, stat_key, raw_value, rank, league_size,
       direction, display_label, unit, refreshed_at
  FROM public.team_computed_stats
 WHERE NOT (sport IN ('NFL', 'NCAAF') AND season < 2026);

NOTIFY pgrst, 'reload schema';
