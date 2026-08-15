-- Advanced sabermetrics fields (2026-08-15).
--
-- 5-part refinement pack applied to mlb_game_context. Each field is
-- computed by mlb_advanced_metrics.py (new module) and consumed
-- downstream by projection layer + prop pipeline.
--
-- ITEMS (in order shipped):
--   1. Third-Time-Through-The-Order (TTTO) penalty
--      * expected_starter_ip_home / _away — derived from L5 avg IP/start
--      * ttto_penalty_runs — how many runs added to projected_total to
--        account for expected TTTO exposure (starter throwing past ~19 BF)
--   2. Catcher framing → K prop bonus
--      * home_framing_k_bonus / _away — expected called strikes lift per
--        game from catcher framing_runs; used by prop scorer for K props
--   3. BABIP regression flag
--      * home_team_babip_l14 / _away — team L14 BABIP
--      * babip_regression_flag — 'hot' | 'cold' | 'neutral' when a team
--        has BABIP > 0.320 or < 0.280 (both regression candidates)
--   4. Bullpen availability score
--      * home_bullpen_availability / _away — 0-100 score based on
--        leverage relievers' rest days (100 = closer + setup rested)
--      * home_bullpen_effective_era / _away — availability-weighted
--        BP ERA (differs from season aggregate when leverage arms taxed)
--   5. SIERA (Skill-Interactive ERA) alongside xERA
--      * home_sp_siera / _away — Skill-Interactive ERA; typically a
--        better forward-projector than xERA (LOB%/sequencing adjusted)
--
-- All fields nullable so pipeline can partially-populate. Projection
-- layer treats null as "signal unavailable, use fallback."

ALTER TABLE public.mlb_game_context
  -- TTTO
  ADD COLUMN IF NOT EXISTS expected_starter_ip_home NUMERIC,
  ADD COLUMN IF NOT EXISTS expected_starter_ip_away NUMERIC,
  ADD COLUMN IF NOT EXISTS ttto_penalty_runs NUMERIC,
  -- Framing → K bonus
  ADD COLUMN IF NOT EXISTS home_framing_k_bonus NUMERIC,
  ADD COLUMN IF NOT EXISTS away_framing_k_bonus NUMERIC,
  -- BABIP
  ADD COLUMN IF NOT EXISTS home_team_babip_l14 NUMERIC,
  ADD COLUMN IF NOT EXISTS away_team_babip_l14 NUMERIC,
  ADD COLUMN IF NOT EXISTS babip_regression_flag TEXT,
  -- Bullpen availability
  ADD COLUMN IF NOT EXISTS home_bullpen_availability NUMERIC,
  ADD COLUMN IF NOT EXISTS away_bullpen_availability NUMERIC,
  ADD COLUMN IF NOT EXISTS home_bullpen_effective_era NUMERIC,
  ADD COLUMN IF NOT EXISTS away_bullpen_effective_era NUMERIC,
  -- SIERA
  ADD COLUMN IF NOT EXISTS home_sp_siera NUMERIC,
  ADD COLUMN IF NOT EXISTS away_sp_siera NUMERIC;

CREATE INDEX IF NOT EXISTS mlb_game_context_babip_flag_idx
  ON public.mlb_game_context (game_date DESC, babip_regression_flag)
  WHERE babip_regression_flag IS NOT NULL AND babip_regression_flag <> 'neutral';

NOTIFY pgrst, 'reload schema';
