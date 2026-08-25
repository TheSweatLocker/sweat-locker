-- ufc_picks grader columns (2026-08-09)
--
-- Adds outcome fields so UFC picks can be graded post-event and
-- feed into cross-sport track record / sharp_fade_audit_trail.
-- Populated by ufc_grader.py after events end.

ALTER TABLE public.ufc_picks
  ADD COLUMN IF NOT EXISTS winner_actual TEXT,          -- 'a' | 'b' | 'draw' | 'nc' (no-contest)
  ADD COLUMN IF NOT EXISTS method_actual TEXT,          -- 'KO' | 'TKO' | 'SUB' | 'DEC' | 'DQ'
  ADD COLUMN IF NOT EXISTS rounds_actual INT,           -- final round the fight ended
  ADD COLUMN IF NOT EXISTS distance_actual BOOLEAN,     -- true if went to decision
  ADD COLUMN IF NOT EXISTS pick_result TEXT,            -- 'W' | 'L' | 'PUSH' (relative to ev_recommended_side)
  ADD COLUMN IF NOT EXISTS graded_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ufc_picks_ungraded_idx
  ON public.ufc_picks (event_date)
  WHERE graded_at IS NULL;

NOTIFY pgrst, 'reload schema';
