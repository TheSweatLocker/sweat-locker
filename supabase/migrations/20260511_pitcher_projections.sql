-- Pitcher offense-class projections (2026-05-11)
-- ============================================================
-- Populated daily by compute_pitcher_class_projections.py. The app reads
-- this on the game-detail screen to render the "Pitcher Scouting" panel —
-- L7 rolling BB/H/K/IP per start plus per-opponent-class projections.
--
-- Apply via Supabase SQL editor.

CREATE TABLE IF NOT EXISTS pitcher_projections (
  pitcher_name           TEXT PRIMARY KEY,
  mlb_id                 INTEGER,
  team                   TEXT,
  l7_rolling             JSONB,    -- {n_starts, avg_ip, avg_bb, avg_hits, avg_k, avg_er, bb_per_9, hits_per_9, k_per_9, era}
  classes                JSONB,    -- {bucket_label: {n, avg_ip, avg_er, avg_outs, avg_k, avg_bb, avg_hits, avg_hr, era_in_class}}
  total_starts_analyzed  INTEGER,
  updated_at             TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pitcher_proj_updated ON pitcher_projections(updated_at DESC);
