-- 2026-07-25 — daily_grades table for auto-grader.
--
-- Auto-populated by grade_daily_card.py after games complete each night.
-- One row per pick per game. Downstream morning audit reads this table
-- so we don't have to manually pull results + grade the card.
--
-- Grade values: W / L / P / PENDING / UNCLEAR

CREATE TABLE IF NOT EXISTS daily_grades (
  id             BIGSERIAL PRIMARY KEY,
  game_date      DATE NOT NULL,
  game_id        TEXT,                -- may be NULL for aggregate props
  pick_type      TEXT NOT NULL,       -- primary_play / mc_high_conf / nrfi_ensemble / prop_prime / prop_strong
  pick_label     TEXT NOT NULL,       -- human-readable pick text
  matchup        TEXT,
  grade          TEXT NOT NULL,       -- W / L / P / PENDING / UNCLEAR
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (game_date, game_id, pick_type, pick_label)
);

CREATE INDEX IF NOT EXISTS idx_daily_grades_date ON daily_grades (game_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_grades_type_grade
  ON daily_grades (pick_type, grade)
  WHERE grade IN ('W','L','P');

-- RLS — permissive anon read/write (matches MLB pattern)
DO $$
BEGIN
  ALTER TABLE daily_grades ENABLE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'RLS enable skipped: %', SQLERRM;
END $$;

DO $$
BEGIN
  DROP POLICY IF EXISTS "daily_grades select all" ON daily_grades;
  CREATE POLICY "daily_grades select all" ON daily_grades FOR SELECT USING (true);
  DROP POLICY IF EXISTS "daily_grades write anon" ON daily_grades;
  CREATE POLICY "daily_grades write anon" ON daily_grades FOR ALL USING (true) WITH CHECK (true);
END $$;

NOTIFY pgrst, 'reload schema';
