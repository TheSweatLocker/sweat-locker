---
name: game-detail-redesign-729
description: "Game detail modal redesign approved 7/29. Mobile-first phone frame, sport-agnostic base + sport-specific extensions. Kills bloat (cross-book SVG chart, standalone NRFI, Log-a-Pick chips). Adds Money Flow (differentiator), Stat Projections, Range-based Score Prediction, Numbers panel."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-29T22:12:37.739Z
---

**Approved 2026-07-29 (v2 mock).** Ready for React Native implementation.

## Artifact
**Mock URL:** https://claude.ai/code/artifact/4c2f66e3-05e7-4a5c-9ecd-d7158df59946

## Section order (top → bottom)

1. **Sticky Header** — matchup + local ET time + close button. Team names color-coded (away = blue, home = purple).
2. **HERO VERDICT** — big play + tier badge + one-line why. `Tampa Bay Rays ML -149` + "4/4 handicappers on TB. Sharp money 72% + public 73% align on Tampa."
3. **ALIGNMENT STATUS STRIP** — horizontal-scroll chips: Handicappers count, Money side, Models split, ALIGNED verdict. Instant read.
4. **PREDICTED SCORE (RANGE, not avg)** — "TEX 3.5–4.5 · TB 4.5–5.3". Sub: "Top lens (Jerry, 60% lifetime): TEX 4.5 — TB 4.7". Honest uncertainty. Never a blind consensus number.
5. **STAT PROJECTIONS** — pitcher K/ER/BB/H/Outs + team offense R/H. Uses `mlb_game_context.*_pitcher_projected_*`. Placed before Money Flow so user has projection context.
6. **MONEY FLOW** (differentiator) — dual bars per market (ML/RL/Total). Sharp badge fires on ≥+10pp divergence. Divergence note explains when it fires.
7. **LINE MOVEMENT** — opening → current delta strip. Kills cross-book SVG chart.
8. **MODEL CONSENSUS** — 5 lens grid (Panel/Jerry/v3/v4/MC) + consensus one-liner.
9. **HANDICAPPERS** — chips bucketed by side with counts.
10. **STARTING PITCHERS (MLB)** — 2-column pitcher matchup card with L3 ERA + K% + BP.
11. **Cohort Signals** (collapsed) — 2-col grid of fired cohorts + net badge.
12. **Game Props** (collapsed) — prop rows show `Line X · Projected Y` (no shouty %). Link to full Prop Jerry.
13. **Your Book (HRB)** — 3 tiles + parlay CTA. Recommended play highlighted with accent.
14. **All Book Lines** (collapsed) — 10 books, tap to add parlay leg.
15. **📐 Numbers Panel** (collapsed) — full 5-model table + all MC probabilities + cohort breakdown. Depth users only.

## Sport-agnostic base
Sections 1-9, 11-15 render for every sport. Section 10 swaps:
- **MLB:** Pitcher matchup (current mock)
- **NFL/NCAAF:** QB vs defense card + injuries
- **NBA/NCAAB:** Pace/net-rating + rest days
- **UFC:** Fighter reads + method/round/distance breakdown
- **NHL:** Goalie matchup + back-to-back chip

Cohort types + stat projections also swap per sport.

## What was killed
- Cross-book SVG line chart (`renderLineMovement` app/index.tsx 9653-9701) — measured book spread, not time drift. Confusing.
- Standalone NRFI/YRFI section (app/index.tsx 13159-13258) — MLB bloat. Rolled into Numbers panel.
- "Log a Pick" chips (app/index.tsx 13259-13273) — redundant with parlay CTA.
- Verbose Sweat Score card (app/index.tsx 12684-13004) — replaced by hero verdict + alignment strip.

## What was kept
- Model lens grid (refined)
- Pitcher matchup (refined typography)
- HRB tiles + parlay CTA
- All-book-lines (moved to collapsed expander)

## Design tokens (mock CSS variables)
Dark theme: `--bg #0e1116`, `--surface #161b23`, `--surface-2 #1c232d`
Accent (Sweat green): `#00c785` · Sharp blue: `#5aa9ff` · Warn amber: `#f0b34a` · Fade red: `#e05561`
Home: `#e8b8ff` (purple) · Away: `#a8d8ff` (blue)
Type: system-ui + ui-monospace for numbers
Layout: mobile-first, max-width ~420px phone frame, single scroll column

## Backend requirements to fully populate
- **Money Flow:** oddscrowd data (LIVE, needs T-45min-per-game pull)
- **Alignment strip:** compute ext consensus + oc side + models_agree → save to game_context
- **Range Score Prediction:** compute min/max/median across models → save or compute on-fly
- **Stat Projections:** MLB LIVE (`*_pitcher_projected_*`); NFL exists in `nfl_generate_props`; NBA/NHL/UFC TBD
- **Numbers Panel:** all fields already stored in `mlb_game_context.mc_probabilities` + `signal_confluence_breakdown`

## Related
- [[project_mc_hc_recalibration_729]] — MC gate feeds Alignment strip
- [[project_cohort_inversion_729]] — cohort inversion feeds Numbers panel
- [[project_confluence_net3_trap_729]] — confluence tier logic
- [[project_align_pattern_729]] (to create) — "ext+money aligned" tier drives Alignment strip badge
- [[project_launch_priorities_july]] — mid-Aug launch dependency

## Implementation notes
Target file: `c:/Users/gomez/SweatShop/app/index.tsx` — replace lines 12675-13328 modal JSX.
Preserve: `openGameDetail` handler (line 9837), `selectedGame` state (line 1607), `ExternalPicksPanel` component (can be repurposed or removed).
Split into subcomponents: `<GameDetailHeader>`, `<VerdictCard>`, `<AlignmentStrip>`, `<ScoreRange>`, `<StatProjections>`, `<MoneyFlow>`, `<LineMovement>`, `<LensGrid>`, `<HandicappersRow>`, `<PitcherMatchup>`, `<CohortsPanel>`, `<GamePropsPanel>`, `<YourBookTiles>`, `<AllBookLines>`, `<NumbersPanel>`.
