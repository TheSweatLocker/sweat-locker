---
name: casual-bettor-ux-docket
description: "Translation-not-simplification UX layer. Keep all the depth (Sweat Score, cohort %, drivers) — just translate the jargon so casuals can read it. Drafted 2026-06-09 pre-launch, refined same evening after founder pushback."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Refined direction (2026-06-09):** Founder explicitly pushed back on simplifying the product. The principle is **TRANSLATE THE LANGUAGE, KEEP THE DEPTH**. No prescriptive tout language ("bet this to win that"), no dropping the Sweat Score number, no Beginner/Power mode split. Same data surface, different vocabulary. Depth IS the differentiator — the moat is showing the work, just in English a non-power-user can follow.

## What stays (do NOT touch)

- Sweat Score number + tier name — that's the brand. Users come to see "PRIME · 88"
- All driver bullets (currently 6 per game) — they're the receipts
- Cohort hit rate + sample size — the math is the credibility
- All Pitcher Scouting / Situational / Numbers panel depth
- Power-user terms in audit notes (for analysts who DO want them)

## What changes (server-side translation table)

Every driver label runs through a translation pass before the app reads it. Same field, plain English:

| Power label (kept in audit) | User-facing label |
|---|---|
| "v3+v4 DOG consensus" | "Both our prediction models pick the underdog" |
| "Cohort signal confirms" | **"Matches a 73% historical pattern (50-14 over 64 games)"** |
| "PEAK confluence" | "8 of 8 independent signals point this direction" |
| "xera_gap_loud" / "Major xERA gap" | "Major pitcher mismatch (3.15-run xERA gap)" |
| "v3 market disagreement" | "Our v3 model sees this 2.9 runs different from Vegas" |
| "Long-rest ace as DOG" | "Top-tier starter on extra rest is the underdog (62% historical hit rate)" |
| "Fragile starter sweet spot" | "Starter has 1st-inning ERA over 6.0 — vulnerable early" |
| "PEAK confluence UNDER bias" | "8-signal confluence games skew UNDER 58% historically" |
| "NRFI sweet spot" | "Strong NRFI projection — clean 1st inning expected" |
| "v3+v4 tot consensus" | "Both math models agree on the total direction" |

Server writes a `casual_label` and (optionally) keeps the `power_label` for analyst mode. App reads `casual_label` by default.

## What gets ELEVATED (UI hierarchy change)

**Cohort hit rate becomes the second most prominent number after Sweat Score.**

Currently buried inside detail strings. Should be a callout panel:

```
📊 Pattern Match
   73% historical hit rate
   50-14 over 64 games
```

Big, bold, second-position. Receipts visible at a glance.

## Tooltip system on every acronym

Tap "xERA" → "Expected ERA — adjusts for quality of contact a pitcher allows. Lower is better." One-tap learn. The depth gets explained on demand without being dumbed down.

Apply to: xERA, wRC+, OAA, BAA, K%, NRFI, YRFI, BP, EPA (future NFL), DVOA (future NFL), eFG% (future NBA), etc.

## Glossary page (Settings → Glossary)

One-time read for users who want depth all at once. Searchable. Pulls from same translation table.

## Onboarding (deferred — wait until everything else settles)

When ready, the onboarding walkthrough should explain:
1. What the Sweat Score is (it's a confidence rating, not a recommendation)
2. What the Cohort Engine does (pattern matching against history)
3. How to read a pick card (translate the layout)
4. Where to find the depth (analyst mode, glossary)

NOT a "how to bet" tutorial. We're an analytics product.

## What was REMOVED from the original draft (per founder feedback)

- ❌ "Take Pirates ML +138 ($100 → $238)" prescriptive headline — too tout-y
- ❌ Dollar payouts everywhere — users compute their own bet size
- ❌ Drop the (NN) suffix from tier names — Sweat Score IS the brand
- ❌ Beginner/Power mode toggle — splits the product

## Implementation order

**Pre-launch v1.0:**
1. Server-side translation table for driver labels (highest impact)
2. Elevate cohort hit rate to second-position UI element
3. Tooltips on top 8 acronyms (xERA, wRC+, OAA, BAA, K%, NRFI, BP, conf)

**v1.0.1:**
4. Glossary page
5. Remaining tooltips

**v1.1+:**
6. Onboarding walkthrough redesign

## Related
- [[feedback_brand_tagline]] — "More Data, Less Sweat" framing speaks to clarity without sacrificing depth
- [[feedback_backside_dictates_app_renders]] — required pattern for translation work
- [[project_sweat_dimensional_redesign]] — sweat tier architecture this layers on
- [[project_post_launch_roadmap_may_to_nfl]] — fits into v1.0 → v1.2 cadence
- [[project_cohort_engine_universal_architecture]] — cohort engine IS the analytical moat across sports
