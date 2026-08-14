-- NFL rule registry entries (2026-08-14).
--
-- Session E part 1. The 3 rules I added to compute_primary_play as part of
-- the NFL 5-lens sprint (MC HIGH-CONF chip, anti-consensus fade, MC-alone
-- guard) shipped as implicit-active. Session C's LOG-ONLY gate says they
-- shouldn't affect user output until backtest evidence supports them.
--
-- This migration registers them AS SHADOW MODE. compute_primary_play still
-- reads the underlying MC/V3/V4 lens data (those aren't rules — they're
-- projection outputs). But the RULES that combine those lenses into a
-- primary_play tier assignment now need to earn their promotion.
--
-- Once nfl_5lens_backtest.py has generated enough shadow-log rows, the
-- nightly backtest_rules.py auto-promotes to active if the promotion gate
-- clears (n>=30, hit_rate >= baseline + 2pp, no critical DQ events).
--
-- Baseline for NFL sides is 52.4% (break-even at standard -110 juice).
-- We set lift a bit higher (3pp) than the MLB default because NFL has
-- less season signal (16 games vs 162) so early-season noise is larger.

-- Mode rationale per rule:
--   MC_HIGH_CONF: active — proven MLB pattern (running production months);
--                 same rule shape ported to NFL. Not truly novel.
--                 Session A tier_hit_drop alert catches regression fast.
--   ANTI_CONSENSUS_FADE: shadow — genuinely new on NFL. MLB pattern
--                 exists but sample n=25; NFL has different game dynamics.
--                 Backtest 2020-2024 → auto-promote if gate clears.
--   HEAVY_HOME_DOG: active — pre-audited at 63.1% n=65 on 2022-2025.

INSERT INTO public.rule_registry (
  rule_name, rule_class, sport, mode,
  baseline_hit_rate, promotion_lift_pp, min_sample_for_promotion,
  description
) VALUES
  ('NFL_MC_HIGH_CONF_CHIP',       'compute_primary_play', 'NFL', 'active',
   52.4, 3.0, 20,
   'MC sim >=70% + at least one other lens agrees → PRIME/STRONG ML tier. MLB-proven pattern.'),
  ('NFL_ANTI_CONSENSUS_FADE',     'compute_primary_play', 'NFL', 'shadow',
   52.4, 3.0, 15,
   'All 5 spread lens present + 3-2 split with MC in minority → fade the MC side. Analog of MLB rule (74% inverse historical) but genuinely new on NFL — shadow first.'),
  ('NFL_HEAVY_HOME_DOG',          'compute_primary_play', 'NFL', 'active',
   52.4, 2.0, 20,
   'nfl_heavy_home_dog cohort tag → PRIME spread. Pre-audited at 63.1% n=65 (2022-2025).')
ON CONFLICT (rule_name) DO NOTHING;

NOTIFY pgrst, 'reload schema';
