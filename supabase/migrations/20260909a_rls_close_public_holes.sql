-- 2026-09-09: Close the 4 RLS holes surfaced by Supabase Advisor.
-- All 4 tables are readable via anon key today (confirmed).
-- These are internal-only tables (grading outcomes, KenPom raw, UFC results,
-- catcher framing) — no user should touch them directly. Server-role only.

-- Enable RLS + deny-by-default (no policies = nothing readable/writable
-- from anon; service_role bypasses RLS as always).

ALTER TABLE public.outcomes           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kenpom_cache       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ufc_fight_results  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mlb_catcher_framing ENABLE ROW LEVEL SECURITY;

-- Explicit revoke on the anon role for belt-and-suspenders (RLS alone is
-- sufficient with no policies, but revoke removes the grant so PostgREST
-- won't even attempt).
REVOKE ALL ON public.outcomes           FROM anon, authenticated;
REVOKE ALL ON public.kenpom_cache       FROM anon, authenticated;
REVOKE ALL ON public.ufc_fight_results  FROM anon, authenticated;
REVOKE ALL ON public.mlb_catcher_framing FROM anon, authenticated;

NOTIFY pgrst, 'reload schema';
