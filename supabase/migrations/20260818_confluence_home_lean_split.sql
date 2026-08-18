-- 2026-08-18: split confluence_home_lean into fav-vs-dog variants.
--
-- Motivation: baseline analysis of 1,347 graded MLB games shows huge
-- fav/dog asymmetry in home ML hit rates vs break-even:
--   Mod fav (-150 to -199):  56.4% vs 63.6% BE  →  -7.2pp FADE zone
--   Mod dog (+150 to +199):  42.4% vs 36.4% BE  →  +6.0pp BACK zone
--
-- The existing confluence_home_lean signal (firing when the cohort net
-- favors HOME by ≥4) applied the same weight regardless of whether the
-- home team is a fav or a dog. That's why the playbook picked BOTH
-- Rockies+172 (dog, correct-directionally) AND Twins-150 (mod fav, in
-- the trap zone) with equal confidence on 8/18.
--
-- Fix: seed two sibling signals with fav/dog gates. Both start
-- UNVALIDATED; backfill_signal_tiers grades each independently. When
-- fired data accumulates, one may VALIDATE while the other stays
-- UNVALIDATED (or ANTI_VALIDATED) — playbook consumes whichever earned
-- its weight. Existing confluence_home_lean stays live for now as a
-- combined baseline (so we can A/B against the split).

BEGIN;

-- signal_sources doesn't have a unique constraint on (signal_key, sport),
-- so ON CONFLICT can't reference one. Delete-then-insert is fully
-- idempotent for this use case.
DELETE FROM signal_sources
 WHERE sport = 'MLB'
   AND signal_key IN (
     'confluence_home_lean_as_fav',
     'confluence_home_lean_as_dog',
     'confluence_home_lean_as_coin'
   );

INSERT INTO signal_sources (
  signal_key, class, sport, market_scope, subject_scope,
  condition_expr, side_expr, strength_expr,
  description, display_prose_template,
  enabled
) VALUES
  (
    'confluence_home_lean_as_fav',
    'cohort',
    'MLB',
    'ml',
    'game',
    --Fires when home is a mod-fav or heavier AND the cohort favors HOME.
    -- Excludes slight-fav (-110 to -149) which is closer to coin regime.
    'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) >= 2 '
    'and ctx.home_ml_close is not None and int(ctx.home_ml_close) <= -150',
    '"HOME_ML"',
    '0.6',
    'Cohort net favors HOME by 4+ AND home is a moderate/heavy favorite (-150 or worse). '
    'Split from confluence_home_lean on 2026-08-18 to isolate fav vs dog regime hit rates.',
    'cohort favors home + home is a moderate favorite',
    TRUE
  ),
  (
    'confluence_home_lean_as_dog',
    'cohort',
    'MLB',
    'ml',
    'game',
    --Fires when home is any dog AND the cohort favors HOME (contrarian
    -- home-dog spot). Historically the +EV bucket per baseline analysis.
    'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) >= 2 '
    'and ctx.home_ml_close is not None and int(ctx.home_ml_close) >= 110',
    '"HOME_ML"',
    '0.7',
    'Cohort net favors HOME AND home is priced as a dog (+110 or bigger). '
    'Historically +6pp above break-even in the mod-dog bucket. '
    'Split from confluence_home_lean on 2026-08-18.',
    'cohort favors home + home priced as underdog (contrarian spot)',
    TRUE
  ),
  (
    'confluence_home_lean_as_coin',
    'cohort',
    'MLB',
    'ml',
    'game',
    --Fires in the pick''em / slight-fav zone (-149 to +109). Baseline
    -- showed coin-flip games are HOME-biased-losers (43% HW vs 51% BE).
    -- Seed as a separate signal so we can measure whether cohort filter
    -- rescues it or if the whole bucket is just anti-home.
    'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) >= 2 '
    'and ctx.home_ml_close is not None and int(ctx.home_ml_close) > -150 '
    'and int(ctx.home_ml_close) < 110',
    '"HOME_ML"',
    '0.5',
    'Cohort favors HOME in the slight-fav to pick-em zone (-149 to +109). '
    'Seeded 2026-08-18 for backfill visibility — this bucket has historically '
    'been HOME-adverse at baseline.',
    'cohort favors home in slight-fav or pickem regime',
    TRUE
  );

COMMIT;

NOTIFY pgrst, 'reload schema';
