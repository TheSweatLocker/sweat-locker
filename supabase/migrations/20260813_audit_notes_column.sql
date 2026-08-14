-- Jerry audit_notes column (2026-08-13).
--
-- Fixes the "audit-tag leakage" bug (surfaced tonight in Expo QA):
-- five repair scripts (apply_refit_verdict_override, conviction_calibration_pass,
-- jerry_pre_publish_audit --repair, collapse_sharp_fade_violations,
-- collapse_pitcher_thesis_contradictions) OVERWRITE short_read with an
-- audit-trail note starting with "[Auto-<class> YYYY-MM-DD ...".
--
-- Users saw garbage like "[Auto-refit-override 2026-08-10 FORCE_PASS_CONFLICT:
-- raw=50 refit=22.4 ..." where they expected Jerry's analyst take.
--
-- Client-side scrubbing shipped earlier (970c7a1b · scrubJerryText helper)
-- as an immediate patch. This migration + follow-up script updates give
-- the proper fix: repair scripts write to a NEW audit_notes column,
-- short_read stays clean with the original analyst prose.
--
-- Design:
--   short_read   — user-facing Jerry analyst take (never mutated by repair)
--   long_read    — user-facing extended take (same rule)
--   audit_notes  — internal audit trail; repair scripts APPEND (never
--                  overwrite) with a "\n---\n<tag>" delimiter so multi-
--                  repair histories chain cleanly
--
-- App can optionally surface audit_notes as an expandable "why did this
-- get flagged" panel — but never in the primary prose slot.
--
-- Applied to both jerry_reads (game-level) + prop_jerry_reads (prop-level).

ALTER TABLE public.jerry_reads
  ADD COLUMN IF NOT EXISTS audit_notes TEXT;

ALTER TABLE public.prop_jerry_reads
  ADD COLUMN IF NOT EXISTS audit_notes TEXT;

-- Optional: index for queries like "show me all rows with an audit note today"
CREATE INDEX IF NOT EXISTS jerry_reads_audit_notes_present_idx
  ON public.jerry_reads (generated_at DESC)
  WHERE audit_notes IS NOT NULL;

CREATE INDEX IF NOT EXISTS prop_jerry_reads_audit_notes_present_idx
  ON public.prop_jerry_reads (generated_at DESC)
  WHERE audit_notes IS NOT NULL;

NOTIFY pgrst, 'reload schema';
