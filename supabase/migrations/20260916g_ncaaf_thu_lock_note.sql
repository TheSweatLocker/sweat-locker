-- NCAAF Jerry Wed-lock UI note (2026-09-16)
--
-- Sister migration to 20260902b_nfl_thu_lock_note.sql — same pattern
-- for NCAAF. NCAAF cards lock Wed 8am ET (analyst read set for the
-- weekend slate) through Tue EOD. Read regenerates only on key
-- player-status changes (starting QB / RB1 / WR1).
--
-- App reads uiNotes['ncaaf_jerry_lock_note'] and renders on NCAAF
-- game detail right next to JerryReadSection (see GameDetailV2
-- 2026-09-16 reposition).

INSERT INTO public.ui_notes (note_key, note_text, description) VALUES
  ('ncaaf_jerry_lock_note',
   E'\U0001F512 Jerry''s NCAAF read locks Wednesday morning for the weekend slate. Sharp bettors do their homework early — same discipline here. Read regenerates only on key player-status changes.',
   'Shown adjacent to JerryReadSection on NCAAF game detail — explains the Wed-lock discipline. Editable via ui_notes; do not hardcode in app.'),
  ('ncaaf_jerry_lock_short',
   E'\U0001F512 Wednesday lock — regenerates only on key status change',
   'Compact one-line variant for chip / footer contexts where space is tight.')
ON CONFLICT (note_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
