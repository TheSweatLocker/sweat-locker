-- Add projected_ks columns to mlb_game_context so social copy, Jerry reads,
-- and the sweat card all surface the SAME K projection the prop scorer uses.
--
-- Trigger: 2026-05-27 DeGrom incident. Social mentioned "Over the projected
-- Ks" without a specific line. Backing into the projection from his K% × IP
-- yielded ~5.2 Ks; his line was 5.5. He delivered 6 — won the implied bet,
-- but the play was unauditable because no number was committed. Root cause:
-- the K scorer computes `_projected_ks` and stuffs it inside the prop row's
-- `signals` JSONB; downstream surfaces (Jerry struct, social, sweat card)
-- don't have a clean column to read, so each one rebuilt its own ad-hoc
-- estimate or skipped the number entirely. Single source of truth fixes it.
--
-- Populated by mlb_pipeline/patch_projected_ks.py which reads the same JSON
-- cache that generate_props.py uses (built by compute_pitcher_class_projections.py),
-- so the published K-Over prop projection and the social-copy projection
-- are guaranteed identical.

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS home_pitcher_projected_ks NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS away_pitcher_projected_ks NUMERIC(4,1);

NOTIFY pgrst, 'reload schema';
