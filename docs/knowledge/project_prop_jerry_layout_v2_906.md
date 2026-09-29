---
name: project-prop-jerry-layout-v2-906
description: 🎯 9/6 queued — Prop Jerry surface layout + design redesign (structured card was hidden by legacy WHY WE BACK THIS panel; needs coherent single-card design)
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-06T18:33:37.359Z
---

**Queued 9/6:** user directive to redesign the Prop Jerry surface layout + design. Trigger: legacy "WHY WE BACK THIS" bullet panel was showing above the structured Prop Jerry card (with coverage pill + recent_form bar chart + why_bullets), creating a double-render that "doesn't look good."

**Immediate fix landed 9/6**: `WHY WE BACK THIS` now hidden whenever `prop.prop_jerry.input_snapshot.render_sections.reasoning.why_bullets` exists. Legacy panel only renders as fallback when structured data missing. Prevents the ugly double-render for launch.

**Post-launch redesign scope (v2 layout):**

- **Single coherent card** — no legacy bullets alongside structured panel. Structured is canonical.
- **Coverage pill placement** — currently top of the structured card; consider moving to prop header (next to tier badge) so users see signal strength before scrolling.
- **Recent form bar chart** — currently mid-card. Test placement above the reasoning bullets vs below.
- **Why / Risk split** — current render has `why_bullets` and `risk_bullets` as two separate sections. Consider a toggle or accordion so risk is one-tap-away, not visually equal-weight to why.
- **Conviction display** — `render_sections.conviction_display` exists but empty in my sample. Wire it or drop it.
- **Verdict** — `render_sections.verdict` exists but empty in my sample. Same.
- **Pitch-type matchup table** — from [[project-prop-deep-dive-tab-905]], consider integrating here or as a tap-in from this card.

**Reference:** [[reference-lineterminal-prop-ui]] competitive benchmark for prop deep-dive UI patterns. Also [[feedback-casual-bettor-ux-docket]] positioning — translation not simplification.

**Sequence:** post-launch, after MLB Week 2 data validates the current signal quality. Not launch-blocking; the immediate fix makes launch-quality acceptable.

**Files to touch when redesigning:**
- `app/index.tsx` prop card render block (~lines 14200-14400)
- `mlb_pipeline/render_prop_template.py` (emits `render_sections` JSON stashed in `prop_jerry_reads.input_snapshot`) — may need new section shapes
