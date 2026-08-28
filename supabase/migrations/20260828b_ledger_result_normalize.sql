-- Normalize ledger_suggestions.result to Win/Loss/Push (matching every
-- other results table). Was single-letter W/L/P — a footgun for any new
-- code reading this column.
--
-- Aggregator (compute_surface_records.py::_classify) handles both formats
-- so this migration doesn't break existing computations. Any future
-- writer to ledger_suggestions.result must use full names.

UPDATE ledger_suggestions
   SET result = CASE result
       WHEN 'W' THEN 'Win'
       WHEN 'L' THEN 'Loss'
       WHEN 'P' THEN 'Push'
       ELSE result
   END
 WHERE result IN ('W', 'L', 'P');

-- Optional constraint to prevent future single-letter drift.
-- Commented out because Pending is a valid state during grading race.
-- Uncomment after 30d of clean writes to enforce.
-- ALTER TABLE ledger_suggestions
--   ADD CONSTRAINT ledger_result_normalized
--   CHECK (result IS NULL OR result IN ('Win', 'Loss', 'Push', 'Pending'));

NOTIFY pgrst, 'reload schema';
