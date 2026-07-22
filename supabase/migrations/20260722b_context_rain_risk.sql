-- 2026-07-22 — Rain risk fields for mlb_game_context.
--
-- 7/21 postponement wiped POTD Red Sox ML + STRONG YRFI BAL@BOS + SKIP
-- prop Warren PIT@NYY in one night (13% of slate + 3 headline picks).
-- rain_risk_flag lets the POTD selector prefer dry games.
--
-- Populated by game_context.get_weather_forecast() at write time via
-- OpenWeatherMap 5-day/3-hour forecast. Threshold 0.40 pop at kickoff
-- (calibrate against actual postponement rate after 30d of data).

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS rain_prob_at_kickoff NUMERIC,
  ADD COLUMN IF NOT EXISTS rain_risk_flag       BOOLEAN;

CREATE INDEX IF NOT EXISTS idx_mlb_game_context_rain_risk
  ON mlb_game_context (rain_risk_flag)
  WHERE rain_risk_flag = TRUE;

NOTIFY pgrst, 'reload schema';
