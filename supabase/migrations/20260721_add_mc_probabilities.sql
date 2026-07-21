-- 2026-07-21: Add mc_probabilities column to mlb_game_context
-- ============================================================
-- Monte Carlo enrichment writes per-game probability bundle here:
--   { mc_p_over, mc_p_under, mc_p_home_win, mc_p_home_covers,
--     mc_mean_total, mc_std_total, mc_computed_at }
-- Stored as JSONB so we can add / drop MC output fields without a schema
-- migration each time. App reads directly from this column via PostgREST.
--
-- Rationale: turning point-estimate projections into actual probabilities
-- users can act on. "OVER 8.5 · 61% MC" is directly usable; "gap +1.2" is
-- not. Foundation from monte_carlo_win_prob.py; enrichment path is
-- enrich_monte_carlo.py (runs post-game_context in cron).
--
-- Idempotent (ADD COLUMN IF NOT EXISTS).

ALTER TABLE public.mlb_game_context
  ADD COLUMN IF NOT EXISTS mc_probabilities jsonb;

-- Optional index on the p_over field for query "give me all high-conviction
-- OVER games today." GIN on jsonb is expensive; leave off unless needed.
-- Callers can index on (mc_probabilities->>'mc_p_over')::float later if
-- that query pattern shows up.

NOTIFY pgrst, 'reload schema';
