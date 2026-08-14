-- User-facing in-app notes (2026-08-14).
--
-- Session D of the pre-launch safety plan. Where Sessions A/B/C were
-- internal monitoring, this is the OUTPUT layer — how monitoring signals
-- become user-facing communication when (and only when) they should.
--
-- Deliberate constraints per user direction (2026-08-14):
--   * NO email (App Store signups = no email access)
--   * NO push notifications (spam risk)
--   * IN-APP ONLY — dismissible card at top of Home tab
--   * DATA-FIRST tone, not customer-service tone (see LANGUAGE_GUARDRAILS.md)
--   * DEFAULT to quiet fix; only surface notes when meaningfully user-visible
--   * FREQUENCY CAPPED so no user sees more than 1 note per 21 days per category
--
-- Two tables:
--   user_notes                — the note LIBRARY (title, body, category,
--                                visibility window, dismissible flag).
--                                One row per (category, cohort, published_at).
--                                Rendered as dismissible card in app when
--                                current time ∈ [publish_at, expires_at).
--
--   user_note_dispatch_log    — per-user tracking of which notes have been
--                                shown/dismissed. Powers frequency capping
--                                and drives the "unread badge" count if we
--                                later add one. NULL user_id = anonymous
--                                (pre-signup) users get the note too.
--
-- Categories: see USER_NOTE_CATEGORIES const in dispatch_user_notes.py.
-- Adding new category = 1 line there + template in the seed section below.

CREATE TABLE IF NOT EXISTS public.user_notes (
  id             BIGSERIAL PRIMARY KEY,
  category       TEXT NOT NULL,        -- 'model_change' | 'losing_streak' | 'new_user_variance' | 'track_record_recap'
  title          TEXT NOT NULL,
  body           TEXT NOT NULL,
  severity       TEXT NOT NULL DEFAULT 'info'
                 CHECK (severity IN ('info','notice','important')),
  cohort         TEXT NOT NULL DEFAULT 'all'
                 CHECK (cohort IN ('all','new_users','paid_subs','test')),
  publish_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at     TIMESTAMPTZ NOT NULL,
  dismissible    BOOLEAN NOT NULL DEFAULT TRUE,
  source_alert_id BIGINT,               -- optional: dashboard_alerts.id that triggered
  context        JSONB,                 -- variable slots (metric values, cohort ids)
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- App query: "what notes should this user see right now?"
CREATE INDEX IF NOT EXISTS user_notes_active_idx
  ON public.user_notes (publish_at DESC, expires_at)
  WHERE expires_at > NOW();

-- Dispatch aggregation: "how many notes per category in last 21 days?"
CREATE INDEX IF NOT EXISTS user_notes_category_recent_idx
  ON public.user_notes (category, publish_at DESC);


CREATE TABLE IF NOT EXISTS public.user_note_dispatch_log (
  id           BIGSERIAL PRIMARY KEY,
  note_id      BIGINT NOT NULL REFERENCES public.user_notes(id) ON DELETE CASCADE,
  user_id      UUID,                    -- NULL = anonymous view
  shown_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dismissed_at TIMESTAMPTZ,             -- populated when user taps dismiss
  UNIQUE (note_id, user_id)              -- one row per (note, user) — prevents dupe
);

-- Per-user recent-notes query for frequency capping
CREATE INDEX IF NOT EXISTS user_note_dispatch_log_user_recent_idx
  ON public.user_note_dispatch_log (user_id, shown_at DESC);


-- RLS: readable by anon (notes are public content); write via pipeline
ALTER TABLE public.user_notes             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_note_dispatch_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_read ON public.user_notes;
CREATE POLICY public_read ON public.user_notes
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.user_notes;
CREATE POLICY public_write ON public.user_notes
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

-- Dispatch log: users can insert their own row (record they saw it) and
-- update their own dismissed_at. Read-only for anon on their own rows.
DROP POLICY IF EXISTS user_read_own ON public.user_note_dispatch_log;
CREATE POLICY user_read_own ON public.user_note_dispatch_log
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS user_write ON public.user_note_dispatch_log;
CREATE POLICY user_write ON public.user_note_dispatch_log
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
