-- Rule registry + shadow log (2026-08-14).
--
-- Session C of the pre-launch safety infrastructure. Makes it structurally
-- impossible for a broken rule to affect user output for weeks without
-- surfacing (the pattern FORCE_FADE_TRAP + FORCE_PASS_CONFLICT followed).
--
-- Rules now have a lifecycle:
--   proposed → shadow (log-only) → active
--                                 ↓ (auto-demoted on backtest fail)
--                                 retired
--
-- In shadow mode, a rule COMPUTES its proposed action + logs to
-- rule_shadow_log but does NOT mutate the pick. After enough fires (default
-- n>=30), backtest_rules.py grades the proposals against actual outcomes.
-- If the rule beats baseline by the required lift, it auto-promotes to
-- active. If it can't, it stays in shadow (or gets retired manually).
--
-- Combined with Session A's rule_fire_stats (which grades ACTIVE rule fires),
-- this closes the loop: bad rules get caught BEFORE they ship, and bad
-- rules already active get caught within days by the fire-stats regression
-- detector.

-- ─── RULE_REGISTRY ────────────────────────────────────────────────────
--
-- One row per rule. mode column drives runtime behavior:
--   'off'    → rule dormant (import stubs still exist, no fires)
--   'shadow' → rule computes + logs to rule_shadow_log, does NOT mutate
--   'active' → rule affects user picks; fires tracked by Session A
--
-- baseline_hit_rate + promotion_lift_pp define the promotion gate.
-- min_sample_for_promotion defines when we're willing to trust the sample.
--
-- Sport = NULL means the rule is cross-sport (like REFIT_BAND_UNPROVEN).

CREATE TABLE IF NOT EXISTS public.rule_registry (
  rule_name                 TEXT PRIMARY KEY,
  rule_class                TEXT NOT NULL,       -- 'refit_override', 'pipeline_repair', 'jerry_synthesis'
  sport                     TEXT,                -- NULL = cross-sport
  mode                      TEXT NOT NULL DEFAULT 'shadow'
                            CHECK (mode IN ('off','shadow','active')),
  activated_at              TIMESTAMPTZ,
  demoted_at                TIMESTAMPTZ,
  baseline_hit_rate         NUMERIC NOT NULL DEFAULT 50.0,
  promotion_lift_pp         NUMERIC NOT NULL DEFAULT 2.0,
  min_sample_for_promotion  INT NOT NULL DEFAULT 30,
  current_hit_rate          NUMERIC,             -- last computed by backtest
  current_sample_n          INT,
  last_backtested_at        TIMESTAMPTZ,
  description               TEXT,                -- 1-line human explanation
  disabled_reason           TEXT,                -- populated when demoted
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Common query: "which rules are eligible for promotion right now?"
CREATE INDEX IF NOT EXISTS rule_registry_shadow_idx
  ON public.rule_registry (mode, current_sample_n DESC)
  WHERE mode = 'shadow';


-- ─── RULE_SHADOW_LOG ─────────────────────────────────────────────────
--
-- Every time a shadow rule "fires" (computes a proposal), we write one row.
-- Later, when the underlying pick grades, we backfill actual_outcome +
-- would_have_hit so backtest_rules.py can compute rule ROI.
--
-- Also captures active-mode fires with applied=true for a complete audit
-- trail — this doubles as the source-of-truth for Session A's
-- rule_fire_stats aggregation (more precise than parsing audit_notes).
--
-- Retention: 180 days. Longer than data_quality_events because we need
-- multi-window backtests (7d/30d/90d) to build reliable promotion gates.

CREATE TABLE IF NOT EXISTS public.rule_shadow_log (
  id                     BIGSERIAL PRIMARY KEY,
  event_ts               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sport                  TEXT NOT NULL,
  game_date              DATE,
  game_id                TEXT,
  rule_name              TEXT NOT NULL,
  rule_mode              TEXT NOT NULL,          -- mode at time of fire
  ---
  target_table           TEXT NOT NULL,          -- 'prop_jerry_reads', 'jerry_reads', ...
  target_id              TEXT NOT NULL,          -- row id or composite key
  ---
  proposed_action        TEXT,                   -- 'FADE→PASS', 'BACK→FADE', 'cap_tier_LEAN'
  before_state           JSONB,
  after_state            JSONB,
  applied                BOOLEAN NOT NULL DEFAULT FALSE,
  ---
  actual_outcome         TEXT,                   -- 'Win' | 'Loss' | 'Push' | NULL pending
  would_have_hit         BOOLEAN,                -- did rule's proposal actually help?
  outcome_backfilled_at  TIMESTAMPTZ,
  ---
  context                JSONB
);

-- One rule can fire multiple times on the same target across days;
-- allow duplicates by not adding a unique constraint.

CREATE INDEX IF NOT EXISTS rule_shadow_log_rule_ts_idx
  ON public.rule_shadow_log (rule_name, event_ts DESC);

CREATE INDEX IF NOT EXISTS rule_shadow_log_pending_grade_idx
  ON public.rule_shadow_log (game_date DESC)
  WHERE actual_outcome IS NULL;

CREATE INDEX IF NOT EXISTS rule_shadow_log_shadow_fires_idx
  ON public.rule_shadow_log (rule_name, event_ts DESC)
  WHERE rule_mode = 'shadow';


-- ─── RLS ─────────────────────────────────────────────────────────────
ALTER TABLE public.rule_registry   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rule_shadow_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_read ON public.rule_registry;
CREATE POLICY public_read ON public.rule_registry
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.rule_registry;
CREATE POLICY public_write ON public.rule_registry
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS public_read ON public.rule_shadow_log;
CREATE POLICY public_read ON public.rule_shadow_log
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.rule_shadow_log;
CREATE POLICY public_write ON public.rule_shadow_log
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);


-- ─── SEED existing rules ─────────────────────────────────────────────
-- Backfill the current rule set into the registry with their known modes.
-- Rules disabled today (FORCE_FADE_TRAP, FORCE_PASS_CONFLICT) go in as
-- 'off' with disabled_reason so the audit trail is preserved.

INSERT INTO public.rule_registry (rule_name, rule_class, mode, description, disabled_reason, baseline_hit_rate)
VALUES
  ('FORCE_FADE_TRAP',              'refit_override',       'off',     'refit<30 + |delta|>=35 → force FADE',
   '2026-08-14: 30d audit hit rate 12%. Disabled. Blind inversion would hit 88%.', 50),
  ('FORCE_PASS_CONFLICT',          'refit_override',       'off',     'refit<45 + |delta|>=20 → force PASS',
   '2026-08-14: killed 63% winners as PASS. Disabled.', 50),
  ('FORCE_PASS_JERRY_HALLUCINATION','refit_override',       'active',  'raw<30 AND refit<30 → force PASS (dual-flag)', NULL, 50),
  ('FORCE_BACK_BOOST',              'refit_override',       'active',  '|delta|>=20 + refit>=80 → force BACK', NULL, 55),
  ('FORCE_BACK_REFIT_OVERRIDE',     'refit_override',       'active',  'FORCE_BACK_BOOST alt path', NULL, 55),
  ('FORCE_BACK_FLIP_LEAN_CAP',      'refit_override',       'active',  'FADE→BACK flip with LEAN cap when band unproven', NULL, 55),
  ('REFIT_BAND_UNPROVEN',           'refit_override',       'active',  'cap LEAN when refit band has <30 graded', NULL, 55),
  ('NO_REFIT_CAP',                  'refit_override',       'active',  'LEAN cap when refit_conviction missing', NULL, 50),
  ('FADE_TYPE_BOMB',                'pipeline_discipline',  'active',  'convert FADE→PASS on prop_types where FADE historically <45%', NULL, 55),
  ('TREND_CONTRADICTS_CRITICAL',    'pipeline_repair',      'active',  'L5 trend contradicts pick direction → force PASS', NULL, 55),
  ('CONTRADICTS_SIM',               'pipeline_repair',      'active',  'MC sim disagrees with pick direction → force PASS', NULL, 55),
  ('KS_UNDER_LOW_CONV',             'pipeline_discipline',  'active',  'ks_under raw conv <60 → force PASS (30d 50% break-even)', NULL, 55),
  ('LAYER_D_PROP_JERRY',            'pipeline_repair',      'active',  'strip generic pitcher references from prop reads', NULL, 50),
  ('STRIPPED_FABRICATED_STATS',     'pipeline_repair',      'active',  'scrub Jerry-fabricated numeric claims', NULL, 50)
ON CONFLICT (rule_name) DO NOTHING;

NOTIFY pgrst, 'reload schema';
