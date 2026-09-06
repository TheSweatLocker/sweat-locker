-- 2026-09-06 user_consent — move consent record from AsyncStorage (device-only)
-- to backend so we retain a legally defensible traceable log across reinstalls,
-- device switches, and support requests. AsyncStorage stays as a local cache
-- but the row here is the authority.
--
-- Written under the anon key (public writes allowed for insert, RLS restricts
-- reads to admin) because consent happens BEFORE any auth in our app —
-- device_id is the primary correlation until sign-in wires up.

CREATE TABLE IF NOT EXISTS public.user_consent (
    id              bigserial PRIMARY KEY,
    device_id       text NOT NULL,                 -- Expo Constants.installationId (anon device fingerprint)
    accepted_at     timestamptz NOT NULL DEFAULT now(),
    terms_version   text NOT NULL,                 -- e.g. 'v1' — bump to force re-consent
    terms_url       text,
    privacy_url     text,
    app_version     text,                          -- from app.json (helps triage which build wrote it)
    platform        text,                          -- 'ios' | 'android'
    user_id         uuid REFERENCES auth.users(id) -- optional; back-filled if consent survives to sign-in
);

CREATE INDEX IF NOT EXISTS user_consent_device_id_idx
    ON public.user_consent (device_id, accepted_at DESC);
CREATE INDEX IF NOT EXISTS user_consent_terms_version_idx
    ON public.user_consent (terms_version);

-- RLS: anon can INSERT (needed pre-auth), only service role reads (support/legal).
ALTER TABLE public.user_consent ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_consent_anon_insert ON public.user_consent;
CREATE POLICY user_consent_anon_insert
    ON public.user_consent
    FOR INSERT
    TO anon, authenticated
    WITH CHECK (true);

-- Owner can read their own rows once auth wires up.
DROP POLICY IF EXISTS user_consent_owner_read ON public.user_consent;
CREATE POLICY user_consent_owner_read
    ON public.user_consent
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

NOTIFY pgrst, 'reload schema';
