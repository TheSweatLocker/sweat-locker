---
name: feedback-prop-family-ban-three-layer
description: Any prop_type family banned from Prop Jerry must be gated in v_mlb_props_publishable view — NOT just in generate_prop_jerry_synthesis
metadata:
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-15T20:06:59.852Z
---

Any prop_type family blocked from surfacing in Prop Jerry MUST be gated in the `v_mlb_props_publishable` view (and `v_nfl_props_publishable` for NFL) — not only in `generate_prop_jerry_synthesis.py._MLB_BANNED_PROP_TYPES`.

**Why:** the app reads from the view, not from `prop_jerry_reads`. A composer-only ban prevents Jerry rows from generating, but the raw `mlb_pipeline_props` rows still flow through the view to the app. When the pipeline scores those families at LEAN+ tier, they render as prop cards with no label / no chart / no formatter → visible lag + crash on volume. Andy hit this 2026-09-15 late afternoon when runs/rbis/total_bases/hr un-banned in the composer alone flooded ~180 rows into Prop Jerry.

**How to apply:** when adding or removing a prop_type family from `_MLB_BANNED_PROP_TYPES`, ALWAYS make the matching edit in `supabase/migrations/*mlb_props_publishable*.sql` Rule 4 (the family blacklist). Also verify the app has:
- `PROP_TYPE_LABELS` entry in `app/index.tsx` (~line 15158) for the category chip
- Dedicated label formatter for `_over` / `_under` variants (~line 15290)

If either is missing, the family stays banned. Do NOT un-ban composer-only — that's the failure mode that shipped the crash.

**Order to safely enable a new family:**
1. Add `PROP_TYPE_LABELS` entry + label formatter in `app/index.tsx`
2. Ship + verify a build in the app
3. Only THEN remove from Rule 4 in view + `_MLB_BANNED_PROP_TYPES`

Related: [[project_lr_under_family_unban_913]] — memoized the "after tabs added" gate but the enforcement layer wasn't in place.
