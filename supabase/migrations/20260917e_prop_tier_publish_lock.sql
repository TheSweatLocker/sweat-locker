-- 2026-09-17e — UNIVERSAL publish-lock: what you saw is what we grade.
-- ==================================================================
-- Andy 9/17 late-PM: "prime prop integrity" issue — the LIVE tier field
-- flips mid-day (generate_props --force wipes LR overrides, backfill
-- reruns promote, cycle repeats) and the grader reads the live tier at
-- query time. So a play a user saw at PRIME 79 all afternoon can vanish
-- from the PRIME record entirely if a mid-day mutation drops it to
-- SKIP before the game grades. Looks like cherry-picking. Brand-killing.
--
-- Andy 9/17: "and this needs to be sport universal" — the same yo-yo
-- affects sides (primary_play tier on game_context tables) across NFL,
-- NCAAF, NBA, NHL, NCAAB, UFC. One shared lock table lets every
-- publisher across every sport call the same API.
--
-- Design: single `publish_lock` table, sport-universal. Publishers
-- (Sweat Card / Sharp Card / POTD / Prop Jerry / Ladder / Ledger /
-- Daily Degen) write ONE row per (sport, market, source_id) the first
-- time a pick appears on a user-visible surface. The grader (per-sport
-- compute_surface_records + agg_daily_records + others) JOINs on the
-- lock and prefers the locked tier + conviction over the live values.
-- If a row was never locked (legacy / non-published), grader falls
-- back to the live tier so nothing breaks.
--
-- Key semantics:
--   * (sport, market, source_id) is unique. FIRST publisher wins.
--   * `published_at` never mutates on re-publish — we want the earliest
--     surface-visible timestamp preserved.
--   * `tier_at_publish` + `conviction_at_publish` are the authoritative
--     values for grading. Live tier can still float.
--
-- market:
--   * 'prop' — source_id = mlb_pipeline_props.id or nfl_pipeline_props.id
--   * 'ml' / 'rl' / 'total' — source_id = <sport>_game_context.game_id
--   * 'ladder' / 'ledger' — source_id = ledger_snapshots.id
-- ==================================================================

CREATE TABLE IF NOT EXISTS public.publish_lock (
    id BIGSERIAL PRIMARY KEY,
    sport TEXT NOT NULL,
    market TEXT NOT NULL,
    source_id TEXT NOT NULL,
    tier_at_publish TEXT,
    conviction_at_publish INTEGER,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 'sweat_card' / 'sharp_card' / 'potd' / 'prop_jerry' / 'ladder' /
    -- 'ledger' / 'daily_degen' — helps trace back which surface saw it
    -- first when we audit later. Multiple surfaces can attempt to
    -- lock; first-wins.
    published_by TEXT
);

-- One lock per (sport, market, source_id). First insert wins — later
-- ON CONFLICT DO NOTHING preserves the earliest publish.
CREATE UNIQUE INDEX IF NOT EXISTS publish_lock_key_uniq
  ON public.publish_lock (sport, market, source_id);

-- Grader hot-path — filtered by sport + market, joined by source_id.
CREATE INDEX IF NOT EXISTS publish_lock_sport_market_idx
  ON public.publish_lock (sport, market, source_id);

-- RLS: readable by anon + authenticated (grader reads with service
-- role but no reason to hide the lock from clients if they ever want
-- to render "this was locked at PRIME 79 on Sept 17 at 9:47am"
-- provenance). Writable only by service role (publishers use it).
ALTER TABLE public.publish_lock ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS publish_lock_read_all ON public.publish_lock;
CREATE POLICY publish_lock_read_all ON public.publish_lock
  FOR SELECT
  TO anon, authenticated
  USING (TRUE);

NOTIFY pgrst, 'reload schema';
