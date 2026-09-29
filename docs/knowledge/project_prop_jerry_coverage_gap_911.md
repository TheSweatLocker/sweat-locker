---
name: project-prop-jerry-coverage-gap-911
description: Prop Jerry composer only writes jerry_reads for ~46 of ~666 publishable MLB props/day; app L5/L10 charts silently miss for the other 620
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-15T16:32:11.251Z
---

**✅ LARGELY RESOLVED 2026-09-15**. Coverage rose from **5% (memo baseline) → 68.6%** through prior fixes (template-path split, deferred edge filter to LLM-only). Remaining 109 misses on 2026-09-15 slate: **103 are banned `hits_under`** (intentional per policy per launch-day directive — hits_under was flagged as loser; my 9/15 audit shows 18-0 PRIME L30d 100% which contradicts and merits reassessment per Andy "reassess in a couple days"). The 6 non-ban misses are individual prop_type gaps on players who DO get jerry_reads for other prop_types (Foster Griffin: has er_under+outs_under, missing bb_over PRIME) — noise-level, not a systemic composer bug.

To close the remaining 34% coverage, un-ban `hits_under` in `_MLB_BANNED_PROP_TYPES` — would jump coverage to ~98% and surface an additional ~103 PRIME plays with graphs per day. Andy's decision.

🚨 Prop Jerry L5/L10 bar-chart gap on the app card (**ORIGINAL 2026-09-11 memo below**):

**Why:** The chart data lives in `prop_jerry_reads.input_snapshot.render_sections.recent_form.rows`. If no `prop_jerry_reads` row exists for a `(game_id, player_name, prop_type, direction)` publishable in `v_mlb_props_publishable`, the app renders the card WITHOUT a chart (fallback WHY-bullets only). User asked why "not seeing graphs in prop jerry L5/L10 bar graphs" — because most props have no matching jerry row.

**Measurements (2026-09-11 MLB slate):**
- `v_mlb_props_publishable`: 666 rows (23 PRIME, 17 STRONG, 49 LEAN, 577 COVERAGE)
- `prop_jerry_reads`: 46 rows total → only 32 view rows have a matching `jerry_short_read`
- Coverage: **32 / 666 ≈ 5%**. 634 rows publishable-but-graphless.
- Pitcher-side is worst: 5-of-8 sampled pitchers had ZERO jerry rows (Imanaga, Lugo, Molina, Springs, Cavalli, Kirby). Nola had 1 (outs_under) while view surfaces 2 different props (er_under, ha_under). Same-player key mismatch = no chart.

**Root cause in `generate_prop_jerry_synthesis.py`:**
- `_keep_skip` drops SKIP-tier props unless bucket_roi says BACK
- `_has_edge` drops COVERAGE unless projection edge ≥ 10% or K% extreme
- Workflow calls with `--tier-gate NONE` which puts every remaining prop on the template path (deterministic, cheap), but the two filters above eat ~95% before we reach that step. The 577 COVERAGE-tier rows in the view mostly never enter the composer's loop.

**How to apply:** For the L5/L10 chart to show on EVERY publishable card, `generate_prop_jerry_synthesis` needs to write template-rendered rows for every row the view surfaces — not just the tier-worthy narratives. Two viable shapes:
  1. Loosen `_has_edge` so template path runs for all COVERAGE regardless of edge (cheap; deterministic template output; keeps LLM budget on PRIME/STRONG only).
  2. Restrict `v_mlb_props_publishable` to `EXISTS (SELECT 1 FROM prop_jerry_reads WHERE keys match)` so we never publish something with no chart.

Option 1 is user-preferred posture — Signal Coverage pill and recent_form chart on every card. Option 2 shrinks the app catalog which conflicts with the "let engine speak" directive.

Related: [[feedback_signal_gate_over_tier_906]] · [[feedback_backside_dictates_app_renders]] · [[project_prop_jerry_layout_v2_906]]
