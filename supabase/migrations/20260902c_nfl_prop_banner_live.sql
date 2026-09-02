-- Update NFL prop banner copy for Week 1 live state (2026-09-02)
--
-- Pipeline IS live: nfl_generate_props.py runs on cron, nfl_pipeline_props
-- table populates, get_upcoming_nfl_pipeline_props RPC returns rows to app.
-- Prior banner ("NFL props — coming this season") lies once Week 1 markets
-- post Wed Sept 3.
--
-- New copy explains: props live but tier-capped Week 1-3 while playbook
-- calibrates. Users see honest expectations without hype.
--
-- Also updates the playbook body message so it doesn't say "still building"
-- when it's actually running.

UPDATE public.ui_notes
SET note_text = 'NFL props are live for Week 1. Playbook calibrates weekly as sample accumulates — early-season picks capped at STRONG tier until Week 3-4 when hit-rate history stabilizes. No PRIMEs until the model has real data to back them.',
    updated_at = now()
WHERE note_key = 'nfl_prop_banner';

UPDATE public.ui_notes
SET note_text = 'NFL prop playbook — early season, tier discipline applied.\nPicks appear as odds post + projections land.\nFull PRIME/STRONG unlocks Week 3-4.',
    updated_at = now()
WHERE note_key = 'nfl_playbook_body';

NOTIFY pgrst, 'reload schema';
