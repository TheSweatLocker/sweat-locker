---
name: ufc-externals-809
description: "UFC external picks — BFO working, ESPN MMA fight-picker + Sherdog + MMAJunkie stubs pending. OddsCrowd does NOT track UFC."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-09T12:44:43.463Z
---

**Confirmed 2026-08-09**: OddsCrowd does NOT track UFC. User confirmed. So sharp-money divergence signal won't work for UFC without an alternative source.

**Current state of `mlb_pipeline/pull_externals_ufc.py`** (existing scaffold, 517 lines):
- ✅ **BFO (Best Fight Odds)**: fetch_bfo() implemented, parses odds consensus per fighter (line 237, 108 LOC). Only source currently producing external_picks with source='bfo' for UFC.
- ❌ **fetch_sherdog()**: STUB (line 346) — public URL structure known but not implemented
- ❌ **fetch_mmajunkie()**: STUB (line 355) — HTML scrape not implemented

**Recommended NEXT source to add (2 hours work)**:
**ESPN MMA fight predictor**. Free per-fight expert picks via:
```
https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/events/{event_id}/competitions/{comp_id}/predictor
```
Sourced from ESPN scoreboard's competitions[] (same endpoint we use in `ufc_grader.py`). Payload includes ESPN expert win-probability predictions per fighter.

**Follow-up options after ESPN** (harder):
- Kambi / DK / FD public splits — need scraping; may not be publicly exposed
- Sherdog fight preview picks — HTML scrape, known URL structure
- MMA Junkie staff picks — HTML scrape
- MMA-punks consensus — commercial subscription
- Twitter handicapper aggregation (Nick Kalikas, Show Me the Money) — manual seed + automation

**Sharp-money proxy alternative**: Without true sharp/public splits, we could:
- Track LINE MOVEMENT direction (open → close) as a proxy for money direction
- Cross-reference to per-book odds spread (widening = sharp side) via ufc_odds_pull data
- Neither replaces true sharp signal but both are computable from existing data

**How to apply**: When user asks about UFC sharp money, remind: not available. If they want that pattern-recognition for UFC, path is (a) ship ESPN predictor puller, (b) build UFC-analog of confluence_net, (c) then extend fade rules to UFC games in `sharp_fade_audit_trail`.

Related: [[project_ufc_high_conf_sweep_809]], [[project_sharp_money_fade_808]].
