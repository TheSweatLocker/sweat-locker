-- NFL Jerry Thu-lock UI note (2026-09-02)
--
-- Adds the note copy explaining Jerry's Thursday-lock behavior to the
-- ui_notes table (backend-editable per project_backend_notes_901).
-- App reads uiNotes['nfl_jerry_lock_note'] and renders on NFL game tab.
-- Can be updated post-launch without an App Store review cycle.
--
-- Behavior it explains (Thu-lock spec, 2026-09-02):
--   1. Jerry generates NFL reads Thursday 8am ET each NFL week
--   2. Reads stay locked (identical) Thursday through Wednesday
--   3. If a starting QB status changes post-lock, that game regenerates
--   4. Sharp bettors think their week early; app matches that discipline

INSERT INTO public.ui_notes (note_key, note_text, description) VALUES
  ('nfl_jerry_lock_note',
   '🔒 Jerry''s NFL read locks Thursday morning for the full week. Sharp bettors do their homework early — same discipline here. Read regenerates only if a starting QB is ruled out.',
   'Shown at top of NFL game detail (or Sharp Card / Sweat Card NFL slot) explaining the Thu-lock discipline. Editable via ui_notes; do not hardcode in app.'),
  ('nfl_jerry_lock_short',
   '🔒 Thursday lock — regenerates only on QB status change',
   'Compact one-line variant for chip / footer contexts where space is tight.')
ON CONFLICT (note_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
