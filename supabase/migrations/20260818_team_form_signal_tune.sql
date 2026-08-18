-- 2026-08-18 (evening): tune the team-form + H2H signals from empirical results.
--
-- Backfill_signal_tiers ran on 30d of enriched data. Two findings:
--
-- 1) H2H_home_dominant (home team won 4+ of last 5 H2H) → ANTI_VALIDATED
--    at 45.2% (n=31, -7.2pp). MEAN-REVERSION: books adjust the line to
--    price in the recent H2H dominance, and the "obvious" side loses.
--    → Add inverse signal h2h_home_dominant_fade that backs AWAY_ML.
--
-- 2) H2H_under_streak (4+ of last 5 H2H went UNDER) → ANTI_VALIDATED
--    at 40.6% (n=69, -11.8pp). "The unders are hitting" is a bad
--    heuristic — hidden regression to mean.
--    → Add inverse signal h2h_over_after_unders that backs OVER.
--
-- 3) Venue-split signals had 0 fires in 30d because the threshold
--    (>=7 wins, <=2) is too tight given team-form churn. Loosening to
--    >=6 / <=3 so they actually fire and produce a real n for validation.
--
-- Original signals stay (ANTI-validated ones still get tracked); the
-- new inverse signals are what the playbook will actually consume.

BEGIN;

DO $$
DECLARE
  s text;
  sports text[] := ARRAY['MLB','NFL','NCAAF','NCAAB','NHL','NBA'];
BEGIN
  FOREACH s IN ARRAY sports LOOP
    -- Clear prior seeds so this is idempotent
    DELETE FROM signal_sources
     WHERE sport = s
       AND signal_key IN (
         'h2h_home_dominant_fade',
         'h2h_away_dominant_fade',
         'h2h_over_after_unders',
         'h2h_under_after_overs',
         'home_ats_hot_at_home',
         'home_ats_cold_at_home',
         'away_ats_hot_on_road',
         'away_ats_cold_on_road'
       );

    -- INVERSE signals — turn ANTI findings into positive-EV picks
    INSERT INTO signal_sources (signal_key, class, sport, market_scope, subject_scope,
      condition_expr, side_expr, strength_expr,
      description, display_prose_template, enabled) VALUES
      ('h2h_home_dominant_fade','h2h',s,'ml','game',
       'ctx.h2h_last5_games_played is not None and int(ctx.h2h_last5_games_played) >= 4 '
       'and int(ctx.h2h_last5_home_wins) >= 4',
       '"AWAY_ML"','0.65',
       'INVERSE of h2h_home_dominant (empirically 45% hit → ANTI at -7.2pp). '
       'Home team recently dominant vs opponent → books price this in → BACK AWAY.',
       'H2H reverse: home has owned recent meetings — book overcorrects, back away', TRUE),
      ('h2h_away_dominant_fade','h2h',s,'ml','game',
       'ctx.h2h_last5_games_played is not None and int(ctx.h2h_last5_games_played) >= 4 '
       'and int(ctx.h2h_last5_home_wins) <= 1',
       '"HOME_ML"','0.55',
       'Companion to h2h_away_dominant (which is +10.9pp DISCOVERY). '
       'Kept for parity; less predictive because books DO underweight away streaks.',
       'H2H reverse: away has owned meetings — modest home fade', TRUE),
      ('h2h_over_after_unders','h2h',s,'total','game',
       'ctx.h2h_last5_games_played is not None and int(ctx.h2h_last5_games_played) >= 4 '
       'and int(ctx.h2h_last5_overs) <= 1',
       '"OVER"','0.65',
       'INVERSE of h2h_under_streak (empirically 40.6% under-streak hit → -11.8pp). '
       'After 4+ unders in H2H → books hold total low → BACK OVER regression.',
       'H2H reverse: 4+ recent unders — book overcorrects, back over', TRUE),
      ('h2h_under_after_overs','h2h',s,'total','game',
       'ctx.h2h_last5_games_played is not None and int(ctx.h2h_last5_games_played) >= 4 '
       'and int(ctx.h2h_last5_overs) >= 4',
       '"UNDER"','0.55',
       'Symmetric: after 4+ overs in H2H, back under. Untested (over_streak had n=1).',
       'H2H reverse: 4+ recent overs — expect regression, back under', TRUE);

    -- LOOSENED venue-split thresholds (>=7 was too tight, no fires in 30d)
    INSERT INTO signal_sources (signal_key, class, sport, market_scope, subject_scope,
      condition_expr, side_expr, strength_expr,
      description, display_prose_template, enabled) VALUES
      ('home_ats_hot_at_home','team_form',s,'rl','game',
       'ctx.home_ats_l10_at_home is not None and int(ctx.home_ats_l10_at_home) >= 6',
       '"HOME_RL"','0.55',
       'Home team covered 6+ of last 10 games AT HOME. Loosened 2026-08-18 '
       'from >=7 → >=6 because tighter threshold produced 0 fires.',
       'home covering at home (L10 at-home ATS)', TRUE),
      ('home_ats_cold_at_home','team_form',s,'rl','game',
       'ctx.home_ats_l10_at_home is not None and int(ctx.home_ats_l10_at_home) <= 3',
       '"AWAY_RL"','0.55',
       'Home team failed to cover 7+ of last 10 AT HOME → back away RL. '
       'Loosened from <=2 → <=3.',
       'home cold at home — back away RL', TRUE),
      ('away_ats_hot_on_road','team_form',s,'rl','game',
       'ctx.away_ats_l10_on_road is not None and int(ctx.away_ats_l10_on_road) >= 6',
       '"AWAY_RL"','0.55',
       'Away team covered 6+ of last 10 ON THE ROAD. Loosened from >=7 → >=6.',
       'away covering on road (L10 road ATS)', TRUE),
      ('away_ats_cold_on_road','team_form',s,'rl','game',
       'ctx.away_ats_l10_on_road is not None and int(ctx.away_ats_l10_on_road) <= 3',
       '"HOME_RL"','0.55',
       'Away team failed to cover 7+ of last 10 ON ROAD → back home RL. '
       'Loosened from <=2 → <=3.',
       'away cold on road — back home RL', TRUE);
  END LOOP;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
