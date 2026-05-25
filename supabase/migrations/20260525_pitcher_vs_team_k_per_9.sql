-- K-rate mastery dimension for K Over/Under props (2026-05-25).
--
-- Companion to the BAA mastery split shipped 2026-05-24 (commit 283d302).
-- ERA mastery is the wrong axis for K props — a pitcher can have a brutal
-- ERA vs a team (e.g., Schlittler vs PHI: 4.50 ERA) while still owning that
-- team on K rate. K props should fade or boost based on K dominance, not ERA.
--
-- get_pitcher_vs_team() in game_context.py already computes k_vs_team and
-- ip_vs_team (sums from gameLog splits) but currently throws them away —
-- only era_vs_team and avg_vs_team get persisted. This migration adds the
-- columns so the K/9-vs-this-team derived metric can land in the row.
--
-- Forward-looking only — no historical backfill. Existing rows stay null;
-- score_pitcher_ks gracefully handles missing values.

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS home_pitcher_vs_team_k_per_9 numeric,
    ADD COLUMN IF NOT EXISTS away_pitcher_vs_team_k_per_9 numeric,
    ADD COLUMN IF NOT EXISTS home_pitcher_vs_team_ip numeric,
    ADD COLUMN IF NOT EXISTS away_pitcher_vs_team_ip numeric;

ALTER TABLE mlb_game_results
    ADD COLUMN IF NOT EXISTS home_pitcher_vs_team_k_per_9 numeric,
    ADD COLUMN IF NOT EXISTS away_pitcher_vs_team_k_per_9 numeric,
    ADD COLUMN IF NOT EXISTS home_pitcher_vs_team_ip numeric,
    ADD COLUMN IF NOT EXISTS away_pitcher_vs_team_ip numeric;

-- Required: PostgREST schema cache reload (per feedback_migration_pgrst_reload).
-- Without this, the new columns 400 on writes until cache refresh.
NOTIFY pgrst, 'reload schema';
