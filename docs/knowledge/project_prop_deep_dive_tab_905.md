---
name: project-prop-deep-dive-tab-905
description: "Post-launch spec — per-player prop deep-dive tab inside prop card (Jerry read up top, deep stats below)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-06T00:57:22.253Z
---

**Post-launch spec (queued 9/5)**: build a per-player deep-dive screen accessed by tapping a prop card. Positioning: "lead with the read, drop into the data."

**Why**: casual users need Jerry's pick + narrative up front (differentiator vs [[reference-lineterminal-prop-ui]] which stacks jargon and asks user to synthesize). Sharp users want the underlying data below the fold. This tab serves both by ordering.

**How to apply** (when scoping this feature):

Layout (top-to-bottom):
1. **Sweat Locker pick** — call + tier + odds + line (frozen at write time per [[feedback-backside-dictates-app-renders]])
2. **Jerry short_read** (2-3 sentences on why, in casual bettor language)
3. **Recency table**: L3 / L5 / L10 / Season, columns per key metric, ALL / vs LHP / vs RHP toggle
4. **Metric grouping** — Contact Quality / Swing Shape / Outcomes (mirrors [[reference-lineterminal-prop-ui]])
5. **Color-coded strength vs league** (green / yellow / red) with legend; every % shows n per [[feedback-sample-size-with-pct]]
6. **Pitch-type matchup table** — opposing pitcher's pitch usage + hitter's per-pitch AVG/SLG/HR
7. **Hover tooltips** on jargon (xwOBA → "expected batting average based on contact quality")

Data dependencies (mostly wired):
- Recency L3/L5/L10: [[project-rolling-rollup-architecture-901]] matviews cover team-level; extend to player-level
- vs LHP/RHP splits: batter_platoon table exists
- Per-pitch: opposing pitcher's pitch_arsenal + hitter's per-pitch xStats — NEW data model
- League baselines for color coding: needs league_stats_rolling matview

**Do NOT copy**: jargon-first framing, sign-up wall on scroll, static tables. Our pitch is translation-not-simplification — [[feedback-casual-bettor-ux-docket]].

**Sequence dependency**: NOT launch-blocking. Post-launch feature. Prereqs: paywall wired ([[project-app-store-submission-901]]), rolling rollup matviews expanded, pitch-arsenal data ingestion for all sports (currently MLB-only).
