-- Promote call_verdict to a first-class column on jerry_reads (2026-08-16).
--
-- Morning-audit finding: the BACK / FADE / NEUTRAL / PASS verdict lives
-- inside the prose short_read/long_read on synthesis_v1, so the auditing
-- pass had to text-scan for it — low-confidence, brittle. Promote it to a
-- real column so every downstream audit (record-by-verdict, FADE-vs-BACK
-- ROI, etc.) can query directly instead of parsing prose.
--
-- Values: 'BACK' | 'FADE' | 'NEUTRAL' | 'PASS' | null (legacy rows).
-- Backfill nullable — pipeline writer will start populating this on the
-- next synthesis run; older rows stay null and the audit tool can either
-- fall back to prose scanning or exclude them.

ALTER TABLE jerry_reads
    ADD COLUMN IF NOT EXISTS call_verdict TEXT;

COMMENT ON COLUMN jerry_reads.call_verdict IS
  'BACK (bet the model side), FADE (bet the opposite of model), NEUTRAL (market fair, no play), PASS (skip). Populated by synthesis writer; older rows null.';

CREATE INDEX IF NOT EXISTS idx_jerry_reads_verdict
  ON jerry_reads (sport, game_date DESC, call_verdict)
  WHERE call_verdict IS NOT NULL;

NOTIFY pgrst, 'reload schema';
