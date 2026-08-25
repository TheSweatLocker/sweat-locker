-- NHL props table (2026-08-17).
--
-- Mirrors mlb_pipeline_props / nfl_pipeline_props shape so
-- prop_ensemble_scorer.py can score NHL props via the same PROPS_TABLE
-- dispatch. Markets covered:
--   * Goalie: saves, shots_against, goals_allowed
--   * Skater: shots_on_goal, points, goals, assists

CREATE TABLE IF NOT EXISTS public.nhl_pipeline_props (
  id                        BIGSERIAL PRIMARY KEY,
  game_date                 DATE NOT NULL,
  game_id                   TEXT,
  player_name               TEXT NOT NULL,
  player_position           TEXT,           -- 'G' | 'F' | 'D'
  team_abbrev               TEXT,
  opp_abbrev                TEXT,
  matchup                   TEXT,
  prop_type                 TEXT NOT NULL,  -- e.g. 'saves_over', 'sog_over'
  direction                 TEXT NOT NULL,  -- 'over' | 'under'
  prop_line                 NUMERIC NOT NULL,
  book_line                 NUMERIC,
  book_over_odds            INT,
  book_under_odds           INT,
  book_source               TEXT,
  -- Scoring layer
  conviction                NUMERIC,
  refit_conviction          NUMERIC,
  tier                      TEXT,           -- PRIME | STRONG | LEAN | SKIP | COVERAGE
  signals                   JSONB,
  -- L5/L10 lookback (backfill_prop_lookback compatible)
  player_l5_hit_count       INT,
  player_l10_hit_count      INT,
  player_season_hit_pct     NUMERIC,
  player_l5_extreme_flag    BOOLEAN,
  player_l10_extreme_flag   BOOLEAN,
  player_lookback_updated_at TIMESTAMPTZ,
  -- Playbook cross-check (populated by prop_ensemble_scorer shadow)
  result                    TEXT,           -- 'Win' | 'Loss' | 'Push' | 'Pending'
  final_value               NUMERIC,
  resolved_at               TIMESTAMPTZ,
  -- Metadata
  lineup_state              TEXT,
  last_attached_at          TIMESTAMPTZ,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (game_date, player_name, prop_type, direction, prop_line)
);

CREATE INDEX IF NOT EXISTS idx_nhl_props_date ON public.nhl_pipeline_props (game_date DESC);
CREATE INDEX IF NOT EXISTS idx_nhl_props_player ON public.nhl_pipeline_props (player_name, prop_type, game_date DESC);

-- RLS
ALTER TABLE public.nhl_pipeline_props ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.nhl_pipeline_props;
CREATE POLICY public_read ON public.nhl_pipeline_props FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS service_role_write ON public.nhl_pipeline_props;
CREATE POLICY service_role_write ON public.nhl_pipeline_props FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Seed 5 NHL prop signals (reuse prop_ensemble_scorer)
DELETE FROM public.signal_sources
 WHERE sport = 'NHL' AND subject_scope IN ('prop', 'player_prop')
   AND origin = 'SEEDED_NHL_PROP_817';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  ('nhl_player_l10_extreme', 'NHL', 'prop_form', '*', 'player_prop',
   'p.get("player_l10_hit_count") is not None and (int(p["player_l10_hit_count"]) >= 8 or int(p["player_l10_hit_count"]) <= 2)',
   '"BACK" if int(p["player_l10_hit_count"]) >= 8 else "FADE"',
   'abs(int(p["player_l10_hit_count"]) - 5) / 5.0',
   '{player_name} hit line in {player_l10_hit_count}/10 recent games',
   'Player L10 hit rate vs line — extremes trend forward', true, 'SEEDED_NHL_PROP_817'),

  ('nhl_legacy_conviction_strong', 'NHL', 'prop_form', '*', 'prop',
   'p.get("conviction") is not None and float(p["conviction"]) >= 70',
   '"BACK"',
   '(float(p["conviction"]) - 50.0) / 50.0',
   'legacy conviction {conviction} — high-confidence',
   'Reuses legacy conviction as playbook signal', true, 'SEEDED_NHL_PROP_817'),

  ('nhl_goalie_elite_matchup', 'NHL', 'prop_matchup', '*', 'prop',
   'p.get("prop_type", "").startswith("saves_") and isinstance(p.get("signals"), dict) and "goalie_gsaa" in p["signals"]',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.4',
   'goalie {player_name} elite GSAA — expect high save volume',
   'Elite goalie facing high-shot team — backs saves OVER', true, 'SEEDED_NHL_PROP_817'),

  ('nhl_high_pace_matchup', 'NHL', 'prop_environment', 'sog', 'prop',
   'p.get("prop_type", "").startswith("sog_") and isinstance(p.get("signals"), dict) and "pace" in p["signals"]',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.3',
   'high-pace matchup — more shots expected',
   'Both teams high-pace → SOG props trend OVER', true, 'SEEDED_NHL_PROP_817'),

  ('nhl_book_odds_juice_trap', 'NHL', 'prop_form', '*', 'prop',
   'p.get("book_over_odds") is not None and p.get("direction") == "over" and int(p["book_over_odds"]) <= -180 and (p.get("tier") or "").upper() == "PRIME"',
   '"FADE"',
   '0.5',
   'PRIME prop at -180+ juice — historical trap zone',
   'Same batter_hits_juice_trap_803 pattern applied to NHL', true, 'SEEDED_NHL_PROP_817');

NOTIFY pgrst, 'reload schema';
