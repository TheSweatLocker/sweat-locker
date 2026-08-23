-- Fix pitcher signal templates to show real values, not threshold band labels
--
-- 2026-08-23 user audit of Bryce Miller Under 5.5 Ks card exposed:
--   WHY UNDER · "Pitcher L3 ERA 5.50+ — recent slide, bleeds runs"
--   vs. WHY WE BACK THIS · "L3 ERA 8.04 — short leash"
-- Same pitcher, same stat, TWO different values. The 5.50+ version used
-- the signal's threshold value instead of the actual measured value.
--
-- prop_ensemble_scorer.py now injects computed `pitcher_l3_era` /
-- `pitcher_xera` / `pitcher_vs_team_era` fields into the format namespace
-- based on which pitcher (home vs away) the prop's player_name matches.
-- These templates use those tokens to show the real value regardless of
-- side.
--
-- After deploy: same signal fires the same way, prose reads "8.04 L3 ERA"
-- instead of "L3 ERA >= 5.50 —".

UPDATE public.signal_sources
   SET display_prose_template =
       'pitcher L3 ERA {pitcher_l3_era:.2f} — recent slide, bleeds runs'
 WHERE sport = 'MLB' AND signal_key = 'pitcher_recent_cold';

UPDATE public.signal_sources
   SET display_prose_template =
       'pitcher xERA {pitcher_xera:.2f} — short outing risk / bleeds runs'
 WHERE sport = 'MLB' AND signal_key = 'pitcher_xera_high';

UPDATE public.signal_sources
   SET display_prose_template =
       'pitcher tagged by this lineup — vs-team ERA {pitcher_vs_team_era:.2f} on 3+ IP'
 WHERE sport = 'MLB' AND signal_key = 'pitcher_vs_team_tagged_history';

NOTIFY pgrst, 'reload schema';
