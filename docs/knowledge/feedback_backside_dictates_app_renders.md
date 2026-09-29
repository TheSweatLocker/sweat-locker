---
name: feedback-backside-dictates-app-renders
description: "All data decisions (which props to surface, which markets to fetch, which tiers qualify) live server-side. App is a dumb renderer. Recurring violation; user has flagged this multiple times."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Hard rule: **app/index.tsx never makes data decisions.** All filtering, grading, tier-mapping, market-selection, and conviction scoring happens server-side. The app reads a server-built payload and renders it.

**Why:** Every time the app dictates data behavior, we ship hidden filters that silently drop legitimate plays — OR display formats that don't update with our reasoning. Concrete failures:
- 2026-05-26: K-prop labels on Daily Degen showed "6.3 expected Ks (over)" — app was composing labels from raw fields. Sweat card already used "Cease Over 7.5 Ks · proj 8.6" format. Two surfaces, two different formats, same data → user bet -EV because the line we'd take (5.5) wasn't visible. Fix: server composes `signals._display_label`; app reads it verbatim. THIRD violation of this rule.
- 2026-05-21: Sweat card per-game props only fetched 3 hardcoded Odds API markets (`batter_hits`, `batter_home_runs`, `pitcher_strikeouts`). Mize's PRIME H+A Under, Strider's PRIME ER Under, etc. were SCORED by the pipeline but never surfaced because the app hardcoded the market list. Hours later: "any prop in jerry props should be able to pop on sweat card if data is confident in it but it should not be done client side."
- Earlier flag: "no hard numbers in index it should be reading all backside"
- 5/16 cluster: app was computing its own confluence direction display from `spread_delta`; got sign-bug when conventions flipped.

**How to apply:**
- Before adding any filter/threshold/grade in index.tsx, ask: "Should this live in `generate_sweat_card.py` / `compute_primary_play` / `apply_prop_signal_override.py` instead?" Answer is almost always YES.
- **Display labels are a data decision too.** When the format is "Name + line + projection" or any composition of multiple fields, that label should be composed SERVER-SIDE and shipped as a `_display_label` (or equivalent) field. App reads verbatim. Otherwise different surfaces drift into different formats over time. (Pattern enforced 2026-05-26 — server `_display_label` for all prop types in `generate_props.run()` pre-upsert loop.)
- When backend payload is missing a field, **add the field server-side**, not a client computation.
- App fetches from Odds API only for live-only data the pipeline can't precompute (in-game scores, live lines). Everything else is `mlb_pipeline_props`, `mlb_game_context`, `daily_dawg`, `daily_best_bet_history`, `jerry_cache`.
- Pattern: server ships unified arrays (`top_props`, `total_edges`, `skip_alerts`, etc.); frontend maps render functions over them. No client-side filter beyond `.slice(0, N)` for display cap.

**The fix template** (from 2026-05-21 sweat card fix):
1. Backend: build the unified list with all qualifying records, ranked by conviction. Ship as a single field.
2. Frontend: render the field as-is with a dumb formatter. If the field is empty, render nothing. If a new prop type appears, formatter falls through to a generic label — no hardcoded type list.

Related: [[project_props_pipeline_pivot]] — props moved to pipeline-driven scoring; this rule preserves that decision in display layer.
