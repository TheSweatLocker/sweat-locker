---
name: three-cross-sport-jobs-ran-only-inside-the-mlb-workflow
description: "Money-flow scrapers, situational/team-stats matview refresh, and prop-family steps lived in mlb_pipeline.yml or mlb_grade_overnight.yml, so other sports' cards silently depend on baseball running. Found 2026-09-28."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-28T22:06:04.422Z
---

Several jobs that serve **every** sport were invoked only from the baseball workflows. MLB is in the Wild Card round as of 2026-09-28 and ends within a month; when it goes quiet these stop with **no error at all** — the affected cards just freeze on whichever sports are actually in season.

Found and fixed on 2026-09-28:
- **Money-flow scrapers** (`fadereport_scraper.py`, `fadethepublic_scraper.py`, `cleatz_scraper.py`) ran only in `mlb_pipeline.yml`, with no `--sport` so they iterated every sport. NHL money flow was therefore downstream of baseball. Fixed by calling `fadereport_scraper.py --sport NHL` from `nhl_pipeline.yml` (it's the only one of the three that covers NHL — `cleatz` has no NHL choice, `fadethepublic` declares `NO_COVERAGE = ('NHL',)`).
- **`refresh_team_situational_records` + `refresh_team_stats_rolling`** ran only in `mlb_grade_overnight.yml`. Both refresh all sports in one shot. Added to `daily_card.yml` (duplicated, not moved — REFRESH is idempotent and MLB's copy runs right after grading where freshness matters most).

**Why:** this is the same shape as the seven user-facing surfaces extracted into `daily_card.yml` earlier in the week. The pattern to watch for: a step that is sport-agnostic sitting in a sport-specific workflow.

**How to apply:** when auditing a sport's readiness, do not just check that its own workflow has the step — grep every workflow for the script and see which file actually invokes it. `grep -rn "<script>" .github/workflows/`. And check status codes on RPC calls: `refresh_team_situational_records` returned 42809 for six days after the 09-16 matview rename because curl discarded the response, and a 400 looked identical to a 204.

Related: [[project_sweat_card_multisport_921]], [[project_bare_mask_review_920]], [[project_ctx_prop_chain_gap_928]].
