-- NBA props table + signal seeds (2026-08-17).
--
-- Mirrors MLB/NFL/NHL pattern. NBA prop markets are the deepest of any
-- sport — PTS/REB/AST/3PM/plus_minus/PA/blocks/steals/turnovers per
-- player per game. Odds API supports all common ones.

CREATE TABLE IF NOT EXISTS public.nba_pipeline_props (
  id                        BIGSERIAL PRIMARY KEY,
  game_date                 DATE NOT NULL,
  game_id                   TEXT,
  player_name               TEXT NOT NULL,
  team_abbrev               TEXT,
  opp_abbrev                TEXT,
  matchup                   TEXT,
  prop_type                 TEXT NOT NULL,  -- pts_over, reb_under, ast_over, threes_over, etc.
  direction                 TEXT NOT NULL,
  prop_line                 NUMERIC NOT NULL,
  book_line                 NUMERIC,
  book_over_odds            INT,
  book_under_odds           INT,
  book_source               TEXT,
  -- Scoring
  conviction                NUMERIC,
  refit_conviction          NUMERIC,
  tier                      TEXT,
  signals                   JSONB,
  -- L5/L10 lookback (shared with other sports)
  player_l5_hit_count       INT,
  player_l10_hit_count      INT,
  player_season_hit_pct     NUMERIC,
  player_l5_extreme_flag    BOOLEAN,
  player_l10_extreme_flag   BOOLEAN,
  player_lookback_updated_at TIMESTAMPTZ,
  -- Resolution
  result                    TEXT,
  final_value               NUMERIC,
  resolved_at               TIMESTAMPTZ,
  -- Metadata
  minutes_played            NUMERIC,        -- filled by resolver
  lineup_state              TEXT,
  last_attached_at          TIMESTAMPTZ,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (game_date, player_name, prop_type, direction, prop_line)
);

CREATE INDEX IF NOT EXISTS idx_nba_props_date ON public.nba_pipeline_props (game_date DESC);
CREATE INDEX IF NOT EXISTS idx_nba_props_player ON public.nba_pipeline_props (player_name, prop_type, game_date DESC);

ALTER TABLE public.nba_pipeline_props ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.nba_pipeline_props;
CREATE POLICY public_read ON public.nba_pipeline_props FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS service_role_write ON public.nba_pipeline_props;
CREATE POLICY service_role_write ON public.nba_pipeline_props FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 6 NBA prop signals (reuse pattern from MLB/NFL/NHL)
DELETE FROM public.signal_sources
 WHERE sport = 'NBA' AND subject_scope IN ('prop', 'player_prop')
   AND origin = 'SEEDED_NBA_PROP_817';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  ('nba_player_l10_extreme', 'NBA', 'prop_form', '*', 'player_prop',
   'p.get("player_l10_hit_count") is not None and (int(p["player_l10_hit_count"]) >= 8 or int(p["player_l10_hit_count"]) <= 2)',
   '"BACK" if int(p["player_l10_hit_count"]) >= 8 else "FADE"',
   'abs(int(p["player_l10_hit_count"]) - 5) / 5.0',
   '{player_name} hit line in {player_l10_hit_count}/10 recent games',
   'Player L10 hit rate vs line — extremes trend forward', true, 'SEEDED_NBA_PROP_817'),

  ('nba_legacy_conviction_strong', 'NBA', 'prop_form', '*', 'prop',
   'p.get("conviction") is not None and float(p["conviction"]) >= 70',
   '"BACK"',
   '(float(p["conviction"]) - 50.0) / 50.0',
   'legacy conviction {conviction} — high-confidence signal',
   'Reuses legacy conviction as playbook signal', true, 'SEEDED_NBA_PROP_817'),

  ('nba_high_pace_boost_over', 'NBA', 'prop_environment', '*', 'prop',
   'p.get("direction") == "over" and isinstance(p.get("signals"), dict) and "pace" in p["signals"] and "high" in str(p["signals"].get("pace","")).lower()',
   '"BACK"',
   '0.4',
   'high-pace matchup — more possessions = more counting stats',
   'Both teams high-pace → PTS/REB/AST OVER favored', true, 'SEEDED_NBA_PROP_817'),

  ('nba_low_pace_boost_under', 'NBA', 'prop_environment', '*', 'prop',
   'p.get("direction") == "under" and isinstance(p.get("signals"), dict) and "pace" in p["signals"] and "low" in str(p["signals"].get("pace","")).lower()',
   '"BACK"',
   '0.4',
   'low-pace matchup — fewer possessions = fewer counting stats',
   'Both teams slow-pace → UNDER favored', true, 'SEEDED_NBA_PROP_817'),

  ('nba_book_odds_juice_trap', 'NBA', 'prop_form', '*', 'prop',
   'p.get("book_over_odds") is not None and p.get("direction") == "over" and int(p["book_over_odds"]) <= -180 and (p.get("tier") or "").upper() == "PRIME"',
   '"FADE"',
   '0.5',
   'PRIME prop at -180+ juice — historical trap',
   'Same juice_trap pattern applied to NBA', true, 'SEEDED_NBA_PROP_817'),

  ('nba_starters_out_matchup', 'NBA', 'prop_matchup', '*', 'prop',
   'isinstance(p.get("signals"), dict) and "opp_starter_out" in p["signals"]',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.5',
   'opposing starter out — reduces defensive matchup difficulty',
   'Opp starter injury → PTS/AST/REB OVER favored (softer defense)', true, 'SEEDED_NBA_PROP_817');

NOTIFY pgrst, 'reload schema';
