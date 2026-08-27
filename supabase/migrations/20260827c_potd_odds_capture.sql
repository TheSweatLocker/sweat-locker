-- POTD odds capture: snapshot the American odds at write time so
-- surface_records can compute honest ROI instead of flat -110 assumption.
-- Column is NULLABLE — the aggregator falls back to -110 for historical
-- rows that predate this migration.

ALTER TABLE daily_best_bet_history
  ADD COLUMN IF NOT EXISTS odds_american NUMERIC;

-- Backfill from mlb_game_context.primary_play + close_ml columns where
-- possible. ML picks get real prices; total/spread stay NULL → aggregator
-- reads NULL as -110. Only touches MLB rows since ml_close is MLB-only.
UPDATE daily_best_bet_history hist
   SET odds_american = CASE
       WHEN (ctx.primary_play->>'side') = 'HOME'
            AND (ctx.primary_play->>'type') = 'ml'
            THEN COALESCE(ctx.home_ml_close, ctx.home_ml_open)
       WHEN (ctx.primary_play->>'side') = 'AWAY'
            AND (ctx.primary_play->>'type') = 'ml'
            THEN COALESCE(ctx.away_ml_close, ctx.away_ml_open)
       ELSE NULL
   END
  FROM mlb_game_context ctx
 WHERE hist.sport = 'MLB'
   AND hist.odds_american IS NULL
   AND ctx.game_date = hist.bet_date
   AND (ctx.away_team || ' @ ' || ctx.home_team) = hist.game;

NOTIFY pgrst, 'reload schema';
