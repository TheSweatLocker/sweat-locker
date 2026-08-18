-- 2026-08-18: seed signal_sources rows that read the new venue-split
-- + H2H columns from enrich_team_form_universal.py.
--
-- Universal (all 6 sports) — same conditions, different sport rows.
-- All start UNVALIDATED; backfill_signal_tiers grades them once the
-- enricher populates enough games.
--
-- Signals seeded (per sport):
--   home_ats_hot_at_home        — home team ATS >= 7-3 at home L10
--   home_ats_cold_at_home       — home team ATS <= 2-8 at home L10
--   away_ats_hot_on_road        — away team ATS >= 7-3 on road L10
--   away_ats_cold_on_road       — away team ATS <= 2-8 on road L10
--   home_ml_hot_at_home         — home team ML wins >= 8 at home L10
--   away_ml_hot_on_road         — away team ML wins >= 6 on road L10
--   h2h_home_dominant           — home team won 4+ of last 5 H2H
--   h2h_away_dominant           — away team won 4+ of last 5 H2H
--   h2h_covers_home             — home covered 4+ of last 5 H2H
--   h2h_over_streak             — 4+ of last 5 H2H went over
--   h2h_under_streak            — 4+ of last 5 H2H went under

BEGIN;

DO $$
DECLARE
  s text;
  sports text[] := ARRAY['MLB','NFL','NCAAF','NCAAB','NHL','NBA'];
BEGIN
  FOREACH s IN ARRAY sports LOOP
    -- Clear any prior seeds so this is idempotent
    DELETE FROM signal_sources
     WHERE sport = s
       AND signal_key IN (
         'home_ats_hot_at_home','home_ats_cold_at_home',
         'away_ats_hot_on_road','away_ats_cold_on_road',
         'home_ml_hot_at_home','away_ml_hot_on_road',
         'h2h_home_dominant','h2h_away_dominant',
         'h2h_covers_home','h2h_over_streak','h2h_under_streak'
       );

    -- Home venue ATS
    INSERT INTO signal_sources (signal_key, class, sport, market_scope, subject_scope,
      condition_expr, side_expr, strength_expr,
      description, display_prose_template, enabled) VALUES
      ('home_ats_hot_at_home','team_form',s,'rl','game',
       'ctx.home_ats_l10_at_home is not None and int(ctx.home_ats_l10_at_home) >= 7',
       '"HOME_RL"','0.6',
       'Home team has covered 7+ of last 10 games AT HOME.',
       'home covering at home (L10 at-home ATS)', TRUE),
      ('home_ats_cold_at_home','team_form',s,'rl','game',
       'ctx.home_ats_l10_at_home is not None and int(ctx.home_ats_l10_at_home) <= 2',
       '"AWAY_RL"','0.6',
       'Home team failed to cover in 8+ of last 10 AT HOME → back the away RL.',
       'home cold at home (L10 at-home ATS) — back away RL', TRUE),
      ('away_ats_hot_on_road','team_form',s,'rl','game',
       'ctx.away_ats_l10_on_road is not None and int(ctx.away_ats_l10_on_road) >= 7',
       '"AWAY_RL"','0.6',
       'Away team covered 7+ of last 10 ON THE ROAD.',
       'away covering on road (L10 road ATS)', TRUE),
      ('away_ats_cold_on_road','team_form',s,'rl','game',
       'ctx.away_ats_l10_on_road is not None and int(ctx.away_ats_l10_on_road) <= 2',
       '"HOME_RL"','0.6',
       'Away team failed to cover in 8+ of last 10 ON ROAD → back the home RL.',
       'away cold on road — back home RL', TRUE),
      ('home_ml_hot_at_home','team_form',s,'ml','game',
       'ctx.home_ml_l10_at_home is not None and int(ctx.home_ml_l10_at_home) >= 8',
       '"HOME_ML"','0.55',
       'Home team has won 8+ of last 10 AT HOME.',
       'home dominant at home (L10 at-home ML)', TRUE),
      ('away_ml_hot_on_road','team_form',s,'ml','game',
       'ctx.away_ml_l10_on_road is not None and int(ctx.away_ml_l10_on_road) >= 6',
       '"AWAY_ML"','0.55',
       'Away team has won 6+ of last 10 ON ROAD.',
       'away winning on the road (L10 road ML)', TRUE),
      -- H2H (last 5 meetings, regardless of venue)
      ('h2h_home_dominant','h2h',s,'ml','game',
       'ctx.h2h_last5_games_played is not None and int(ctx.h2h_last5_games_played) >= 4 '
       'and int(ctx.h2h_last5_home_wins) >= 4',
       '"HOME_ML"','0.65',
       'Todays home team has won 4+ of last 5 meetings vs this opponent.',
       'H2H dominant — home team owns this matchup recently', TRUE),
      ('h2h_away_dominant','h2h',s,'ml','game',
       'ctx.h2h_last5_games_played is not None and int(ctx.h2h_last5_games_played) >= 4 '
       'and int(ctx.h2h_last5_home_wins) <= 1',
       '"AWAY_ML"','0.65',
       'Todays home team has lost 4+ of last 5 meetings vs this opponent → back away.',
       'H2H reverse dominance — away owns this matchup', TRUE),
      ('h2h_covers_home','h2h',s,'rl','game',
       'ctx.h2h_last5_games_played is not None and int(ctx.h2h_last5_games_played) >= 4 '
       'and int(ctx.h2h_last5_home_covers) >= 4',
       '"HOME_RL"','0.55',
       'Todays home team has covered spread in 4+ of last 5 vs opponent.',
       'H2H covers home', TRUE),
      ('h2h_over_streak','h2h',s,'total','game',
       'ctx.h2h_last5_games_played is not None and int(ctx.h2h_last5_games_played) >= 4 '
       'and int(ctx.h2h_last5_overs) >= 4',
       '"OVER"','0.6',
       '4+ of last 5 meetings between these teams went OVER.',
       'H2H over streak', TRUE),
      ('h2h_under_streak','h2h',s,'total','game',
       'ctx.h2h_last5_games_played is not None and int(ctx.h2h_last5_games_played) >= 4 '
       'and int(ctx.h2h_last5_overs) <= 1',
       '"UNDER"','0.6',
       '4+ of last 5 meetings between these teams went UNDER.',
       'H2H under streak', TRUE);
  END LOOP;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
