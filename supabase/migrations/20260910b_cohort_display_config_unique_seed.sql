-- 2026-09-10 · Cohort Signal UX pass (per project_cohort_signal_ux_909)
-- Two things happen here:
--   1. Add UNIQUE (sport, tier) so backend seeders can upsert without racing.
--   2. Seed the actually-emitted NFL + NCAAF cohort_tags + confluence keys with
--      plain-english labels + explainer descriptions. Backend dictates render:
--      the app looks up (sport, tier) here instead of inventing labels client-
--      side from raw snake_case tags (which was producing "home fav" chips
--      lowercase next to "Divisional" / "Roof: dome" Title-Case chips — the
--      non-uniform-capitalization bug user flagged 9/10).
--
-- Feedback anchor: feedback_backside_dictates_app_renders.md — app is dumb-
-- renderer, backend owns display. Seed here now, add records column in a
-- follow-up migration once cohort_tag_records rollup lands.

-- 1a. Drop legacy UNIQUE(tier) — same signal key ('hfa', 'off_epa') has different
--     meaning per sport. The old constraint blocks NCAAF/hfa when NFL/hfa exists.
DO $$
DECLARE
  cname text;
BEGIN
  FOR cname IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'cohort_display_config'::regclass
      AND contype = 'u'
      AND array_length(conkey, 1) = 1
      AND conkey[1] = (SELECT attnum FROM pg_attribute
                       WHERE attrelid = 'cohort_display_config'::regclass
                         AND attname = 'tier')
  LOOP
    EXECUTE format('ALTER TABLE cohort_display_config DROP CONSTRAINT %I', cname);
  END LOOP;
END $$;

-- 1b. Add UNIQUE (sport, tier) so backend seeders can upsert without racing.
--     Idempotent — no-op if constraint already exists.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cohort_display_config_sport_tier_key'
  ) THEN
    ALTER TABLE cohort_display_config
      ADD CONSTRAINT cohort_display_config_sport_tier_key UNIQUE (sport, tier);
  END IF;
END $$;

-- 2. Seed rows (INSERT ... ON CONFLICT DO UPDATE so re-runs stay clean)
INSERT INTO cohort_display_config (sport, tier, label, description, group_name, display_order, is_active) VALUES
  -- NFL cohort_tags
  ('NFL', 'nfl_home_fav',        'Home Favorite',
   'Home team is favored on the spread — cohort tracks how home favorites cover.',
   'situation', 10, true),
  ('NFL', 'nfl_heavy_home_dog',  'Heavy Home Underdog',
   'Home team is a +7 or larger underdog — historically a live spot for the home dog to cover.',
   'situation', 20, true),
  ('NFL', 'nfl_div_home_cover',  'Divisional Home Cover',
   'Home team in a divisional matchup — familiarity narrows spreads and drives cover rate.',
   'situation', 30, true),
  -- NFL signal_confluence_breakdown keys (short chip labels + tap-through explainers)
  ('NFL', 'hfa',         'Home-Field Edge',
   'Home-field advantage signal — venue, crowd, and travel factors point one way.',
   'confluence', 100, true),
  ('NFL', 'cpoe',        'QB Accuracy (CPOE)',
   'Completion percentage over expected — which QB is throwing a more accurate ball than the market implies.',
   'confluence', 110, true),
  ('NFL', 'def_splash',  'Defensive Splash',
   'Sacks + INTs + PBUs — which defense is generating more havoc plays per snap.',
   'confluence', 120, true),
  ('NFL', 'off_epa',     'Offensive EPA',
   'Expected Points Added per play on offense — model of who is actually moving the ball efficiently.',
   'confluence', 130, true),
  ('NFL', 'rush_epa',    'Rush EPA',
   'Expected Points Added per rushing play — separates real ground-game edges from box-score rushing totals.',
   'confluence', 140, true),

  -- NCAAF cohort_tags
  ('NCAAF', 'ncaaf_home_fav',        'Home Favorite',
   'Home team is favored on the spread.',
   'situation', 10, true),
  ('NCAAF', 'ncaaf_heavy_home_fav',  'Heavy Home Favorite',
   'Home team is favored by 14+ points — historically a fade spot as chalk struggles to cover.',
   'situation', 15, true),
  ('NCAAF', 'ncaaf_heavy_home_dog',  'Heavy Home Underdog',
   'Home team is a +7 or larger underdog.',
   'situation', 20, true),
  ('NCAAF', 'ncaaf_shootout',        'Projected Shootout',
   'Modeled total 60+ — both offenses forecasted to score at a high rate.',
   'situation', 40, true),
  ('NCAAF', 'ncaaf_grinder',         'Projected Grinder',
   'Modeled total under 45 — both defenses tilt the game toward possessions and field position.',
   'situation', 50, true),
  -- NCAAF signal_confluence_breakdown keys
  ('NCAAF', 'hfa',            'Home-Field Edge',
   'Home-field advantage signal.',
   'confluence', 100, true),
  ('NCAAF', 'sp_plus',        'SP+ Rating',
   'Bill Connelly SP+ efficiency rating — team quality signal.',
   'confluence', 105, true),
  ('NCAAF', 'off_epa',        'Offensive EPA',
   'Expected Points Added per play on offense.',
   'confluence', 110, true),
  ('NCAAF', 'def_epa',        'Defensive EPA',
   'Expected Points Added allowed per play on defense (lower is better).',
   'confluence', 115, true),
  ('NCAAF', 'explosiveness',  'Explosiveness',
   'Rate of big plays — teams that hit chunk gains at an above-average clip.',
   'confluence', 120, true),
  ('NCAAF', 'success_rate',   'Success Rate',
   'Percentage of plays that stay ahead of the down-and-distance schedule.',
   'confluence', 125, true)
ON CONFLICT (sport, tier) DO UPDATE
  SET label         = EXCLUDED.label,
      description   = EXCLUDED.description,
      group_name    = EXCLUDED.group_name,
      display_order = EXCLUDED.display_order,
      is_active     = EXCLUDED.is_active,
      updated_at    = NOW();

-- Refresh PostgREST schema cache so the new constraint is picked up.
NOTIFY pgrst, 'reload schema';
