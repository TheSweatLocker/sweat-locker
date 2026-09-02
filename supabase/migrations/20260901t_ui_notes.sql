-- ui_notes table (2026-09-01) — DB-pullable user-facing copy strings.
--
-- Purpose: any "still building" / "coming this season" / status banner
-- copy that today lives as hardcoded JSX in app/index.tsx needs to be
-- editable WITHOUT an App Store re-review cycle. This table stores
-- keyed copy strings; app loads them at startup + renders with a
-- hardcoded fallback if the DB row is missing or the fetch fails.
--
-- Contract:
--   note_key    unique slug the app references (e.g. 'nfl_prop_banner')
--   note_text   the display string, plain text (no HTML/markup)
--   enabled     bool; false means app falls back to hardcoded default
--   updated_at  bumped on every write so app can invalidate cache
--
-- App usage:
--   const noteText = uiNotes[key]?.text || HARDCODED_FALLBACK;
--   → if row missing or enabled=false, hardcoded fallback wins
--   → if row present + enabled=true, DB copy wins
--
-- Fail-safe: fetch failure = empty map = hardcoded defaults everywhere.
-- App can never break because DB is unreachable.

CREATE TABLE IF NOT EXISTS public.ui_notes (
  note_key     TEXT PRIMARY KEY,
  note_text    TEXT NOT NULL,
  enabled      BOOLEAN NOT NULL DEFAULT true,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  description  TEXT
);

COMMENT ON TABLE public.ui_notes IS
  '2026-09-01: user-facing copy strings editable without app update. App reads on load + falls back to hardcoded defaults if empty/disabled/fetch-failed.';

-- RLS: read-only for anon/authenticated (all copy is public). Service writes only.
ALTER TABLE public.ui_notes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ui_notes_read_public" ON public.ui_notes;
CREATE POLICY "ui_notes_read_public"
  ON public.ui_notes
  FOR SELECT
  USING (enabled = true);

GRANT SELECT ON public.ui_notes TO anon, authenticated;

-- Seed the current hardcoded notes so DB matches app behavior day-1.
-- If any of these need editing later: `UPDATE ui_notes SET note_text = '...' WHERE note_key = '...'`
INSERT INTO public.ui_notes (note_key, note_text, description) VALUES
  ('nfl_prop_banner',
   'NFL props — coming this season. Full playbook (player L4-L6 form, defensive matchup, model projection edges) rolls out during regular season as Week 1-3 sample accumulates.',
   'Shown at top of Prop Jerry tab when NFL sport selected. Editable copy for pre-Week-3 messaging.'),
  ('nhl_prop_banner',
   'NHL props — coming this season. Full playbook (player L10 form, opp goalie save%, line role, PP time) rolls out ahead of October puck drop.',
   'Shown at top of Prop Jerry tab when NHL sport selected. Pre-season expectations messaging.'),
  ('nba_prop_banner',
   'NBA props — coming this season. Full playbook (usage rate, defensive rating vs position, pace-adjusted projections, rest schedule) rolls out ahead of October tipoff.',
   'Shown at top of Prop Jerry tab when NBA sport selected.'),
  ('ncaab_prop_banner',
   'NCAAB props — coming this season. Full playbook rolls out ahead of November season start.',
   'Shown at top of Prop Jerry tab when NCAAB sport selected.'),
  ('nfl_playbook_body',
   'Playbook still building for NFL.\nCheck the note above for the timeline.',
   'Shown in Prop Jerry tab body when NFL selected + no live props yet.'),
  ('nhl_playbook_body',
   'Playbook still building for NHL.\nCheck the note above for the timeline.',
   'Shown in Prop Jerry tab body when NHL selected + no live props yet.'),
  ('nba_playbook_body',
   'Playbook still building for NBA.\nCheck the note above for the timeline.',
   'Shown in Prop Jerry tab body when NBA selected + no live props yet.'),
  ('ncaab_playbook_body',
   'Playbook still building for NCAAB.\nCheck the note above for the timeline.',
   'Shown in Prop Jerry tab body when NCAAB selected + no live props yet.')
ON CONFLICT (note_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
