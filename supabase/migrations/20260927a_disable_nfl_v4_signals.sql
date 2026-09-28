-- 2026-09-27a · Disable the two NFL V4 signal sources.
--
-- V4 IS NOT A MODEL. Measured 2026-09-27 on every nfl_game_context row that
-- carries a value (29 of 316):
--
--     v4_spread   min +1.37   max +1.99   sd 0.197   ALWAYS POSITIVE
--     v3_spread   min -15.74  max +11.43  sd 4.795   (same rows, for scale)
--     v4_confidence = 0.311 on EVERY row
--
-- CHI @ CAR closed at -21.5, v3 projected -15.74, and v4 projected +1.44.
--
-- The cause is in the feature builder, not the model file. v4_features_used
-- contains only:
--     temp, week, wind, season, is_dome, away_bye, div_game, home_bye,
--     away_rest, home_rest, is_playoff, away_short_week, home_short_week
-- Not one team-strength feature — no EPA, no madden, no power_diff, no team
-- identity. v4 cannot know who is playing. +1.5-always-home is simply
-- league-average home-field advantage, the only signal those inputs carry.
-- v4_total behaves identically (45.37 / 47.73, split only by is_dome).
--
-- WHY THIS MATTERS MORE THAN A DARK SIGNAL. These two are enabled, so the
-- card renders "V4 projects +1.44 vs market -21.5" as though a second model
-- disagreed with the market by 23 points. It reads as the strongest dissent
-- on the card while carrying no information at all. 20260926e made that
-- prose MORE prominent while fixing its wording — the wording was corrected
-- without anyone checking whether the number meant anything.
--
-- The internal vote is withheld separately in nfl_game_context.py (v4 is
-- forced to None at its assignment). This migration is the second of the
-- three layers: writer, view/display, and the row itself. Disabling here
-- stops the lens reaching the user even if v4 starts writing again while
-- still feature-blind.
--
-- NOT TOUCHED: v4_model_spread and v4_model_total (sport='MLB'). Those read
-- model_pred_spread / model_pred_total, which are MLB's own model outputs
-- and a different field entirely. The NFL bug does not apply to them.
--
-- TO RE-ENABLE: flip enabled back to true only after v4_features_used shows
-- team-strength inputs AND v4_spread shows real per-game variance (sd well
-- above 0.2, both signs present).

UPDATE public.signal_sources
   SET enabled = false
 WHERE signal_key IN ('v4_spread_nfl', 'v4_total_nfl')
   AND sport = 'NFL';

NOTIFY pgrst, 'reload schema';
