---
name: reference-lineterminal-prop-ui
description: lineterminal.com hitter-detail page — competitive reference for prop deep-dive UI (recency table + handedness filter + pitch matchup)
metadata: 
  node_type: memory
  type: reference
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-06T00:57:03.617Z
---

**lineterminal.com** — competitor prop tool. Hitter-detail screen worth studying for the [[project-prop-deep-dive-tab]] work.

**What their page does well** (screenshot reviewed 9/5):
- L3 / L5 / L10 / Season columns across ~12 metrics — right recency frame
- ALL / VS LHB / VS RHP handedness toggle prominent at top
- Color-coded per-metric strength vs league (green/yellow/red) with legend
- Inline sample-size disclosure (`BBE: L3 5 · L5 6 · L10 12 · S 60`)
- Metrics grouped: Contact Quality / Swing Shape / Outcomes
- Pitch-type breakdown table at bottom (ST Sweeper, FC Cutter usage/AVG/SLG/HR)
- Player header packs identity in one row: photo, jersey #, team, position, handedness, opposing pitcher + handedness

**Where they lose** (deliberate contrast for our positioning):
- Pure Baseball-Savant-lite: no pick, no lean, no narrative, no sharp $
- Jargon-first (mxwOBA, xISO, BBE) — casual bettor bounces
- Aggressive sign-up wall (4th of 20 players)

**How to use**: when building the prop deep-dive tab post-launch, benchmark the table density and color legend against this page. Do NOT copy their jargon-first framing — lead with our pick + Jerry read, drop into stats below. See [[feedback-casual-bettor-ux-docket]] positioning.

**One-idea steal**: per-pitch matchup table (their bottom section) is a signal we don't surface today. Aligns with existing prop_playbook plug-in signal_sources work.
