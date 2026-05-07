-- Offense drift columns — L10 R/G minus season R/G per side
-- ============================================================
-- Catches the "good season offense, cold bats currently" trap that
-- contributed to 5/6 Twins-correlated miss (4 props on a lineup that
-- scored 2 runs in a 15-2 loss). Negative drift = currently cold.
--
-- Read by generate_props.py to fade hits-OVER PRIME picks when the
-- batting team's drift falls below -1.0 R/G — single-team prop stacks
-- on cold offenses are the specific failure pattern.
--
-- Apply via Supabase SQL editor.

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS home_offense_drift NUMERIC,
  ADD COLUMN IF NOT EXISTS away_offense_drift NUMERIC;
