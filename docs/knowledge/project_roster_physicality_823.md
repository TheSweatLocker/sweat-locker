---
name: project-roster-physicality-823
description: "8/23 shadow signal stack for NCAAF/NCAAB — OL/DL weight, frontcourt height, class-year experience. Universal table + weekly ESPN pull. Shadow-mode until 30-45d of hits."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-24T00:44:25.717Z
---

Roster physicality shadow signals shipped 8/23 for NCAAF + NCAAB only (NBA/NFL rejected as duplicative — see below).

**Why:** College recruiting variance creates larger physical mismatches than pro leagues where scouting compresses distributions. NCAAF Weeks 1-3 experience edge is a documented 4-6pp ATS pattern; NCAAB frontcourt height = O-reb + rim protection proxy.

**Architecture:**
- `roster_physicality` table (sport, team, season) with `position_groups` JSONB — sport-universal shape so NBA/NFL can drop in later if edge appears
- `pull_college_rosters.py --sport NCAAF|NCAAB` — single ESPN scraper serves both (uses SPORT_CFG dict pattern, matches [[project_calibration_architecture_805]])
- `enrich_ctx_roster_physicality.py` — populates `home/away_ol_avg_wt`, `ol_dl_weight_gap_home`, `home/away_frontcourt_avg_ht`, `class_year_edge_home` on game_context
- 9 shadow signals in signal_sources (5 NCAAF + 4 NCAAB) with **strength_expr caps at 0.35-0.45** so they can't dominate scoring until reweighted

**Signals:** ncaaf_ol_weight_adv_home/away, ncaaf_heavy_trenches_under, ncaaf_experience_edge_early_home/away (Weeks 1-3 gated); ncaab_frontcourt_height_adv_home/away, ncaab_dual_size_under, ncaab_experience_edge_home/away

**Ports rejected (deliberately):**
- NFL OL weight: PFF grades already ingest this downstream — signal decay
- NBA age curve: market prices peak/decline into season win totals; weak game-to-game edge

**Scrape cadence:** NCAAF Tue only, NCAAB Mon only (rosters don't churn intra-week). Enricher runs on every daily/full cron so new games pick up populated fields.

**Why:** These are theoretical edges without prior hit-rate data. Follows the [[project_playbook_shadow_tracking_820]] discipline pattern — enabled=true but low weight, reweight after 30-45d.

**How to apply:** When user asks about roster/physical attributes for models, this is the pattern; when adding new signals, follow the shadow-first discipline. When reviewing weekly cards during CFB Weeks 1-3, check if experience_edge signals are firing and whether they align with the pick side.
