-- workflow_heartbeat: append-only log of workflow runs so we can detect
-- silent GitHub Actions skips.
--
-- Root cause 8/26 + 8/27: scheduled cron didn't trigger AT ALL for two
-- consecutive mornings. The rescue step in mlb_pipeline.yml only helps
-- when the workflow IS running — a total no-fire never fills props,
-- MC, ladder, ledger, or POTD. Users saw yesterday's stale board.
--
-- Fix: workflow writes a heartbeat row at start + end. A background
-- check (or the app) can query "is there a heartbeat from today?" to
-- alert on missing runs. Also gives us a real audit trail of when
-- pipelines actually fired.

CREATE TABLE IF NOT EXISTS public.workflow_heartbeat (
  id           BIGSERIAL PRIMARY KEY,
  workflow     TEXT NOT NULL,           -- 'mlb_pipeline' | 'ncaaf_pipeline' | ...
  event        TEXT NOT NULL,           -- 'start' | 'end'
  sport        TEXT,                    -- 'MLB' etc (optional)
  run_id       TEXT,                    -- GH Actions run id
  cron_slot    TEXT,                    -- '10' / '1230' / '18' — which cron fired
  fired_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  meta         JSONB
);

CREATE INDEX IF NOT EXISTS workflow_heartbeat_workflow_fired
  ON public.workflow_heartbeat (workflow, fired_at DESC);

NOTIFY pgrst, 'reload schema';
