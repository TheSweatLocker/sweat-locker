-- sharp_fade_audit_trail: per-game per-sport record of every fade-rule
-- trigger + eventual outcome (2026-08-09).
--
-- Purpose:
--   1. Track EVERY game's sharp signals + rule fires + result so we can
--      compute per-rule ROC over time (self-calibration).
--   2. Enable cross-sport comparison as UFC / NFL / NCAAF pipelines
--      onboard.
--   3. Give operator a full audit trail: "why did we cap X on 8/8?"
--   4. Enable retrospective analysis: "which rule set would have
--      maximized ROI over the last 30d?"
--
-- One row per (sport, game_id). Backfill from historical snapshots is
-- possible via sharp_fade_rules_stats replay. Live rows written by
-- sharp_pattern_dashboard.py at pick time (before game starts) then
-- backfilled with outcome by nightly grader.
--
-- Schema is sport-agnostic. UFC picks use game_id = 'ufc_2026-08-08_12'
-- convention; MLB uses raw game_id hash. Both work.

CREATE TABLE IF NOT EXISTS public.sharp_fade_audit_trail (
  id BIGSERIAL PRIMARY KEY,

  -- Identity
  sport TEXT NOT NULL,                           -- MLB / UFC / NFL / NCAAF / NCAAB
  game_id TEXT NOT NULL,
  game_date DATE NOT NULL,
  matchup TEXT,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Pick under evaluation (usually Jerry's call)
  pick_source TEXT DEFAULT 'jerry',              -- jerry / prop / potd / manual
  pick_market TEXT,                              -- ml / total / spread / rl / prop
  pick_side TEXT,                                -- HOME/AWAY/OVER/UNDER + fighter for UFC
  pick_line NUMERIC,
  pick_conviction_pre_cap INT,
  pick_conviction_after_cap INT,

  -- Sharp signal snapshot at time of pick
  sharp_ml_pick TEXT, sharp_ml_div INT, sharp_ml_money INT, sharp_ml_bets INT,
  sharp_total_pick TEXT, sharp_total_div INT, sharp_total_money INT, sharp_total_bets INT,

  -- Fade-rule triggers (JSONB list, [{rule, mode, severity, reason}])
  rules_triggered JSONB DEFAULT '[]'::jsonb,
  active_rules_count INT DEFAULT 0,
  cap_directive TEXT,                            -- CAP_TO_LEAN_55 / CAP_TO_READ_49 / NULL
  bucket_flag JSONB,                             -- current bucket-cap payload

  -- Outcome (backfilled after game completes)
  actual_ml_winner TEXT,
  actual_total_result TEXT,                      -- OVER / UNDER / PUSH
  actual_home_score INT,
  actual_away_score INT,
  ml_sharp_won BOOLEAN,
  total_sharp_won BOOLEAN,
  pick_won BOOLEAN,                              -- did the tracked pick hit?
  cap_was_correct BOOLEAN,                       -- capping was right call in hindsight?
  resolved_at TIMESTAMPTZ,

  UNIQUE (sport, game_id)
);

CREATE INDEX IF NOT EXISTS sfat_date_sport_idx
  ON public.sharp_fade_audit_trail (game_date DESC, sport);
CREATE INDEX IF NOT EXISTS sfat_cap_idx
  ON public.sharp_fade_audit_trail (cap_directive)
  WHERE cap_directive IS NOT NULL;
CREATE INDEX IF NOT EXISTS sfat_unresolved_idx
  ON public.sharp_fade_audit_trail (game_date)
  WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS sfat_rules_gin_idx
  ON public.sharp_fade_audit_trail USING gin (rules_triggered);

NOTIFY pgrst, 'reload schema';
