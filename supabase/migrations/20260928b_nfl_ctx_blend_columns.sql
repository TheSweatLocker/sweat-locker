-- 2026-09-28b · nfl_game_context blend label + games-played columns.
--
-- The exact NFL twin of 20260909e_ncaaf_ctx_blend_columns.sql, which shipped
-- for NCAAF on 09-09 and was never mirrored to NFL. nfl_game_context.py has
-- been writing all four fields since then:
--
--   nfl_game_context.py:1595  row['home_games_played']
--   nfl_game_context.py:1596  row['away_games_played']
--   nfl_game_context.py:1597  row['home_stats_blend_label']
--   nfl_game_context.py:1598  row['away_stats_blend_label']
--
-- and pgrst_strip_retry has been dropping all four on every single run:
--
--   ⚠ nfl_game_context: stripped missing cols (away_games_played,
--     away_stats_blend_label, home_games_played, home_stats_blend_label)
--
-- So the disclosure is computed correctly and then discarded, every run,
-- silently — the write "succeeds" because the strip-and-retry helper exists
-- to keep a schema lag from blocking ingest. Useful behaviour; it also means
-- a missing column can go unnoticed for nineteen days.
--
-- WHY THIS MATTERS TODAY, not just as tidiness. Andy's PHI @ CHI QA asked
-- why Jerry's Chicago rushing number disagreed with the number in the stats
-- table. Root cause was _nfl_blend_pg blending the prior season for model
-- stability — CHI rush = 0.667 × 212.5 + 0.333 × 144.5 = 189.82 — which is
-- the right input for a model and the wrong number for prose. The label
-- these columns carry is precisely the sentence that would have explained
-- it ("blended · 3 games this season + 2025 season"), and it never reached
-- the app because the column did not exist. The fix shipped in f4cee594
-- hands Jerry unblended stats directly; this makes the blend itself
-- disclosable wherever it is still used.
--
-- Matches the NCAAF migration exactly, including int over smallint for
-- games_played, so the two sports stay diffable.

ALTER TABLE nfl_game_context
  ADD COLUMN IF NOT EXISTS home_stats_blend_label text,
  ADD COLUMN IF NOT EXISTS away_stats_blend_label text,
  ADD COLUMN IF NOT EXISTS home_games_played int,
  ADD COLUMN IF NOT EXISTS away_games_played int;

COMMENT ON COLUMN nfl_game_context.home_stats_blend_label IS
  'Human-readable provenance for the blended team stats, e.g. "blended · 3 '
  'games this season + 2025 season". Written by _nfl_blend_label(). Exists '
  'so prose and UI can disclose that a displayed stat is a blend rather than '
  'this season only — the ambiguity behind the 2026-09-28 PHI @ CHI QA.';
