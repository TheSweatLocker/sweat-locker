-- admin_notice — invisible-when-empty in-app banner for live operational messages.
--
-- 2026-08-21 background: user needs a way to push a short live note into the
-- app (stale-pick warning, brief outage FYI, quick heads-up) without an app
-- submission cycle. Persistent enough to survive app relaunches, ephemeral
-- enough to auto-hide once expired. App polls / reads on load; if no row is
-- active in the window, banner stays hidden.
--
-- MVP: one row = one message. Multiple concurrent messages allowed (app can
-- show top-severity first, or stack). starts_at/expires_at bound the visible
-- window so we don't have to manually clear stale notes.

CREATE TABLE IF NOT EXISTS public.admin_notice (
  id             BIGSERIAL PRIMARY KEY,
  message        TEXT NOT NULL,
  severity       TEXT NOT NULL DEFAULT 'info'
                 CHECK (severity IN ('info', 'warning', 'critical')),
  starts_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at     TIMESTAMPTZ,           -- NULL = indefinite until explicitly cleared
  dismissible    BOOLEAN NOT NULL DEFAULT TRUE,
  sport          TEXT,                  -- optional scope: 'MLB'/'NFL'/etc. NULL = all sports
  route          TEXT,                  -- optional scope: 'sharp_card'/'externals'/etc. NULL = app-wide
  created_by     TEXT,                  -- for audit; free-form ('andy', 'cron', etc.)
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Active-window index — the app query pattern is:
--   SELECT ... WHERE starts_at <= NOW() AND (expires_at IS NULL OR expires_at > NOW())
-- No WHERE predicate: partial-index predicates require IMMUTABLE expressions
-- and NOW() is STABLE. A plain btree on (starts_at, expires_at) still serves
-- the query well since active rows are the recent tail of the table.
CREATE INDEX IF NOT EXISTS admin_notice_active_window_idx
  ON public.admin_notice (starts_at, expires_at);

-- RLS: anon can read active notices (needed for app); only service_role writes.
ALTER TABLE public.admin_notice ENABLE ROW LEVEL SECURITY;

CREATE POLICY admin_notice_read_active
  ON public.admin_notice
  FOR SELECT
  USING (starts_at <= NOW() AND (expires_at IS NULL OR expires_at > NOW()));

-- Sample usage (from your Supabase console or a script):
--   INSERT INTO admin_notice (message, severity, expires_at) VALUES
--     ('Sharp Card locking early tonight — awaiting late line moves',
--      'warning', NOW() + INTERVAL '3 hours');
--   UPDATE admin_notice SET expires_at = NOW() WHERE id = 42;  -- dismiss

COMMENT ON TABLE public.admin_notice IS
  'Live in-app banner messages. Invisible when no rows in the active window. Set expires_at to bound visibility; NULL for indefinite. severity=info/warning/critical for color.';

NOTIFY pgrst, 'reload schema';
