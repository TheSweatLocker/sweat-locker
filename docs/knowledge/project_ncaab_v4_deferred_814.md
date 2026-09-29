---
name: project_ncaab_v4_deferred_814
description: NCAAB launches Nov 3 with 4-lens stack. V4 XGBoost target moved from Feb → Dec 3 2026 (~1 month post first games).
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-24T00:03:03.143Z
---

NCAAB v1 launches Nov 3, 2026 with a 4-lens stack (MC + Panel + KenPom-in-context + Jerry). V4 XGBoost target: **~Dec 3, 2026** (~1 month post first Nov 3 games). Updated 2026-08-23 from original Feb 2027 timeline per user call to accelerate.

**Why Dec 3 (moved up from Feb):**
User wants V4 online within a month of season start rather than 3+ months. That gives ~200-400 graded games as training set — enough for meaningful XGBoost training on our accumulated point-in-time features. Not "the ideal 4+ months" but the tradeoff (get a working V4 into user hands 2+ months sooner) is worth the reduced sample.

**Why 4-lens is still valid (unchanged):**
- MLB launched with fewer lenses and did fine
- Jerry counts as a lens
- MC + Panel + KenPom + Jerry gives 4 independent perspectives
- V4 will be lens #5 layered on top when it lands

**Data situation (unchanged):**
Historical daily snapshots don't exist for our 3 sources:
- KenPom paid API returns end-of-season only via `y=YYYY`
- Torvik JSON is season-end only
- Haslam XML is always current (no history)

Since 8/14 we've been capturing our own clean daily snapshots via [[project_ncaab_panel_814]] scrapers. By Dec 3 we'll have ~3.5 months of Nov+early-Dec point-in-time features PLUS the pre-season Aug 14 → Nov 3 snapshots. Adequate training set.

**How to apply:**
- Do NOT sink hours into KenPom `d=` param archive scraping or Wayback Torvik/Haslam (leaky features not worth it)
- Do NOT ship V4 with lagged-season features (roster turnover in CBB makes this too noisy)
- Nov 3 launches with 4-lens stack (MC + Panel + KenPom-in-context + Jerry)
- **Dec 3 target: train + ship V4 as lens #5** from accumulated ncaab_rating_snapshots
- If Dec 3 has <200 games graded, push to Dec 15 or Jan
- 4-lens is not "lens-poor" — MLB launched with fewer

**KenPom API discipline (unchanged):**
- Monday-only cron (kenpom_only mode) — previously ran 2× daily
- Torvik + Haslam continue daily (free scrapers)
- Panel model tolerates mixed snapshot dates
- V4 backfill: plan API calls carefully, don't spam paid tier
