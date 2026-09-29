---
name: project-ncaab-scope
description: "NCAAB product scope locked for v1.0/v1.1 — spreads, totals, moneyline only. NO player props. Reaffirmed 2026-05-22."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

NCAAB launches November 2026 with a strict scope: **spreads, totals, moneyline only.** No player props.

**Why no NCAAB player props:**
- Sportsbooks don't reliably offer college player props (limited market depth)
- College player data is sparser than NBA/MLB (no advanced player tracking comparable to Statcast/Synergy)
- Models would be lower-quality than our MLB props
- Would dilute the "great model" positioning to ship weak props alongside strong game-side picks

**What NCAAB v1.0/v1.1 ships:**
- Spread picks (vs market line)
- Total over/under picks
- Moneyline picks
- POTD candidates from the above
- Cohort tracking on the above (no prop cohorts)

**Code implications:**
- `ncaab_game_context.py` does NOT compute prop projections
- `ncaab_pipeline_props` table is intentionally not created
- `getResolvedPropsTable('NCAAB')` returns null (already in sportPeriods.ts)
- Curate_top_8 equivalent for NCAAB will skip the prop fill step — picks come from game-side primary plays only
- App displays "Spread / Total / ML" focus for NCAAB

**Naming rule reminder (2026-05-22):** Even in product mockups / chat descriptions, never write player props for NCAAB ("Cooper Flagg O 22.5 pts" is forbidden language). Stick to game-side picks. Also: never reference KenPom by name in user-facing copy — see [[feedback_no_kenpom_attribution]].

**Future consideration:** If sportsbooks ever expand NCAAB props meaningfully (post-NIL data infrastructure matures), we revisit. Likely 2027+ timeline.
