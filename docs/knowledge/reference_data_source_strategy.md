---
name: reference_data_source_strategy
description: Per-sport data source decisions + cost matrix lives in docs/data_source_strategy.md. Consult before adding any new sport or evaluating a new paid data feed.
metadata:
  type: reference
---

The full per-sport data strategy doc lives at `docs/data_source_strategy.md`.

Authoritative for:
- Which sources we pay for vs use free
- Build status per sport + migration tracker
- Decision heuristics for evaluating new paid sources
- Steady-state cost snapshot

Key decisions captured (as of 2026-06-16):
- **MLB:** MLB Stats API + Savant + pybaseball (all free, best-in-class)
- **NBA:** Offseason migration BDL → `nba_api` (free, richer). BDL paused 6/16, re-evaluate Oct.
- **NFL:** nfl_data_py only. NO PFF ($800/yr) — nflverse covers needs.
- **NCAAB:** KEEP KenPom ($25/yr) for internal model input. BartTorvik free backup. Never name KenPom in app (see [[feedback_no_kenpom_attribution]]).
- **NCAAF:** cfbd-api free tier (queued Aug 2026)
- **NHL:** NHL Stats API + MoneyPuck (free) — added to roadmap 6/16 after user clarified NHL doesn't need paid feed
- **UFC:** ufcstats.com scrape (free, brittle)
- **Odds API:** Only universal paid line item (~$50/mo target tier)

Target steady-state data COGS: ~$52/mo vs $200-400/mo for "pay-for-everything" stacks.

Related: [[project_nba_offseason_rebuild]] [[project_ncaaf_scope]] [[project_kenpom_pull_no_api_key]] [[feedback_no_kenpom_attribution]]
