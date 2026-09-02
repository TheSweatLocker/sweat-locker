-- Vault Match shadow-mode flag (2026-09-01) — guardrail #5
--
-- Ships the Vault Match chip in SHADOW MODE: attach_vault_matches.py
-- writes matched_patterns[] to ctx rows (so we can audit), but the app
-- silent-hides the chip until this flag flips to enabled=true.
--
-- Rationale: even with corrected outcome_fn + Wilson lower bounds +
-- freshness check + juice-vig threshold, we should audit the actual
-- attached patterns against manual truth for a validation window
-- before showing users. If the compute has any residual bug, we
-- surface no false positives while auditing.
--
-- To flip live after audit:
--   UPDATE public.feature_flags
--   SET enabled = true, updated_at = now()
--   WHERE sport = 'ALL' AND feature = 'vault_render';
--
-- To flip per-sport (e.g. enable MLB but keep NFL/NCAAF shadow):
--   INSERT INTO public.feature_flags (sport, feature, enabled)
--   VALUES ('MLB', 'vault_render', true)
--   ON CONFLICT (sport, feature) DO UPDATE SET enabled = true;

INSERT INTO public.feature_flags (sport, feature, enabled)
VALUES ('ALL', 'vault_render', false)
ON CONFLICT (sport, feature) DO NOTHING;

NOTIFY pgrst, 'reload schema';
