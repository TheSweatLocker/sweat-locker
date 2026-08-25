-- NCAAF weather enrichment columns (2026-08-25).
--
-- Mirrors nfl_game_context weather fields. Populated by ncaaf_weather_pull.py
-- (OpenWeather API) for outdoor stadiums; domed stadiums get default constants.
--
-- Idempotent — safe to re-apply.

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS temp             NUMERIC,
  ADD COLUMN IF NOT EXISTS wind             NUMERIC,
  ADD COLUMN IF NOT EXISTS dome             BOOLEAN,
  ADD COLUMN IF NOT EXISTS weather_source   TEXT;

CREATE INDEX IF NOT EXISTS idx_ncaaf_ctx_weather_windy
  ON public.ncaaf_game_context (game_date, wind)
  WHERE wind IS NOT NULL AND wind >= 15;

NOTIFY pgrst, 'reload schema';
