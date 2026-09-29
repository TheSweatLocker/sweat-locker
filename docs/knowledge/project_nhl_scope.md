---
name: project_nhl_scope
description: NHL is on the roadmap — uses FREE NHL Stats API + MoneyPuck. User clarified 6/16 they don't need to pay for NHL data. Add when capacity allows; not v1.0 launch blocker.
metadata:
  type: project
---

NHL was previously deferred under the (wrong) assumption that NHL data required paid feeds. User clarified 2026-06-16: free reliable sources exist.

**Free stack:**
- **NHL Stats API** (statsapi.web.nhl.com) — official, no auth, schedules / scores / boxscores / lineups / play-by-play / shot data
- **MoneyPuck.com** — free expected goals (xG), high-danger chances, lineup data; serious shop quality
- **Natural Stat Trick** — free advanced stats site, scrapeable
- **Hockey Reference** — free historical scrape

**Scope (proposed v1.0):**
- Spread (puck line), ML, Total (over/under goals)
- Goalie props (save %, shutout) — Vegas books carry these
- Player props (shots on goal, points) — Phase 2 if data quality holds

**When to build:**
- Not a v1.0 launch blocker
- Sequencing: after NFL Phase 2 + NCAAF v1.0 (Aug-Sep build window)
- Target NHL v1.0 for the 2026-27 season opener (early Oct 2026)
- Season window already in [season_calendar.py](season_calendar): Sep 15 - Jun 25

**Cohort considerations:**
- Goalie rest cohorts (back-to-back, three-in-four)
- Travel + altitude (less than NBA but still real for NHL)
- Powerplay efficiency mismatches
- Use universal cohort architecture (see [[project_cohort_engine_universal_architecture]])

Related: [[reference_data_source_strategy]] [[project_cohort_engine_universal_architecture]]
