-- 2026-09-26d · Let team_computed_stats OVERRIDE the matview per stat_key.
--
-- Andy, on two separate cards:
--   "539 yds/g -> 58th pct"
--   "PASS 266 -> 41st, RUSH 192 -> 41st, TOTAL 457 -> 42nd ... the rate
--    stats on the same card calibrate correctly. The break is isolated to
--    the three per-game yardage rows plus turnovers."
--
-- He localised it correctly. team_stats_rolling_full computes these as
-- ncaaf_team_stats.<cumulative> / ncaaf_team_stats.games. The numerator is
-- CFBD's full season-to-date total; the denominator is backfilled from
-- ncaaf_game_results, which only holds FBS results. For an FCS team that
-- is a whole season of yardage divided by 1:
--
--     South Dakota State   2033 "yards per game"
--     Tarleton State       1991
--     UT Rio Grande Valley 1912
--
-- 83 of 216 rows had a denominator wrong by a factor, and they took the
-- best ~90 ranks, which is why Ohio State's 523 yds/g rendered 55th and
-- Auburn's 457 rendered 42nd. The rate stats never divide by `games`,
-- so they calibrated fine on the same card — exactly as Andy observed.
--
-- WHY NOT FIX THE MATVIEW. That is the correct home, but
-- team_stats_rolling_full has been amended by ~10 migrations and its live
-- definition cannot be read from the pipeline host. Rewriting it from a
-- reconstruction is the CREATE OR REPLACE drift trap
-- (feedback_publishable_view_drift) with a matview's blast radius: any
-- rule not restated is silently deleted, and the failure shows up as
-- missing stat rows on a Saturday. So the corrected values are computed
-- in recompute_ncaaf_per_game_stats.py and written to
-- team_computed_stats, and this migration gives that table precedence.
--
-- THE OVERRIDE IS PER (sport, season, stat_key), NOT PER TEAM. That is
-- deliberate. rank and league_size are only meaningful relative to one
-- ranking universe, so mixing computed rows and matview rows inside a
-- single stat_key would produce two incompatible rank scales in one
-- column — a team ranked 40/133 sitting next to one ranked 40/216. When
-- a stat_key is computed, the computed set is the whole universe for it.
--
-- Teams excluded from the trustworthy universe therefore return NO row
-- rather than a fabricated one. GameDetailV2 already hides a stat row
-- when both sides are empty and renders one-sided otherwise, so an
-- FBS-vs-FCS card shows the FBS team's real number against a blank.
-- That is honest, and strictly better than the current state where the
-- FCS column shows 2033 and wins the comparison.
--
-- REVERSIBLE: deleting the rows for a stat_key hands it straight back to
-- the matview, with no further schema change. When the matview is
-- eventually rebuilt with a correct denominator, that DELETE is the
-- entire rollback.
--
-- Carries forward, verbatim, both rules already on this view:
--   * the pre-2026 football filter from 20260916a
--   * the UNION of the computed half added by 20260926c (SOS/SOR)

CREATE OR REPLACE VIEW public.team_stats_rolling AS
SELECT f.sport, f.team, f.season, f.stat_key, f.raw_value, f.rank,
       f.league_size, f.direction, f.display_label, f.unit, f.refreshed_at
  FROM public.team_stats_rolling_full f
 -- Rule carried from 20260916a: football is current-season only.
 WHERE NOT (f.sport IN ('NFL', 'NCAAF') AND f.season < 2026)
   -- …and stand aside for any stat_key the computed table owns.
   AND NOT EXISTS (
        SELECT 1
          FROM public.team_computed_stats c
         WHERE c.sport    = f.sport
           AND c.season   = f.season
           AND c.stat_key = f.stat_key)
UNION ALL
SELECT c.sport, c.team, c.season, c.stat_key, c.raw_value, c.rank,
       c.league_size, c.direction, c.display_label, c.unit, c.refreshed_at
  FROM public.team_computed_stats c
 WHERE NOT (c.sport IN ('NFL', 'NCAAF') AND c.season < 2026);

NOTIFY pgrst, 'reload schema';
