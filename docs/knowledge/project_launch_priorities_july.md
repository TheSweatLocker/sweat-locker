---
name: launch-priorities-july-2026
description: "Launch punch list + order (July 2026). Target public launch mid-August, aligned with NFL v2.0 marketing moment."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Target public launch: mid-August 2026** (~5-6 weeks from 2026-07-02). Aligns with NFL v2.0 release timeline so MLB + NFL share a marketing moment. May 2026 target (see [[project_launch_may_week1]]) is dead.

**Why:** 3-day model performance audit on 2026-07-02 exposed: PRIME props 9-7 (56%) last 2 days below 64% baseline; Panel totals 52% coinflip; 5-source consensus MIA ML failed catastrophically (COL 14-4); recurring pitcher-team attribution bugs; POTD noPlay bug shipped to production twice in a row (7/1 + 7/2). MLB engine has real edge (PRIME props 64% lifetime on 411 picks) but delivery layer isn't launch-ready.

**How to apply:** For any pipeline / app / infra work over next 6 weeks, ask: does this ship a BLOCKER, HIGH-VALUE, or POST-LAUNCH item? Defer POST-LAUNCH unless it's <30min or unblocks a higher-tier item.

## BLOCKERS (must ship — ~1 week)
1. **RLS security migration** — Supabase warning 2 days overdue as of 7/2. Tier 1 lowest-risk batch (`cohort_display_config`, `prompt_templates`) starts tomorrow AM (7/3).
2. **TestFlight deploy** — `main` ahead of last build; today's noPlay app fix + backend fix not in a build users have. See [[project_pending_app_changes]].
3. **Ops stability sprint** — 7/2 morning cron ran incomplete (props table had 1 row until manual re-run). Idempotent retry + failure alerts + morning-cron completeness check.

## HIGH-VALUE (should ship before launch — ~2-3 weeks)
4. **Unified 3-tier taxonomy (PRIME/STRONG/LEAN)** — atomic PR with legacy scrub. See [[project_unified_taxonomy_decision]]. Live tier names lie about confidence.
5. **Consensus-trap detector** — 7/2 MIA ML lesson: when ALL sources agree, downgrade not amp. Add to POTD selector + resolver.
6. **Public track record page** — data exists (`mlb_pipeline_props.result`, `daily_best_bet_history`), not surfaced. THIS is the paywall justification.
7. **Attribution validator on ALL surfaces** — POTD covered ([[project_jerry_attribution_validator]]); extend to per-game reads, prop Jerry, sweat drivers. See [[feedback_verify_pitcher_attribution]].
8. **Weather forecast fix** — [[project_weather_snapshot_bug]] still open, model input broken on night games.
9. **Jerry write-up simplification audit** — casual bettor UX ([[project_casual_bettor_ux_docket]]).
10. **2pm cron overwrite bug** — [[project_pm_cron_live_game_prop_overwrite]] still open.

## POST-LAUNCH (v1.1 / v1.2)
- External Consensus MVP (7 sources) — currently manual scrape via WebFetch works
- Sweat driver cleanup audit
- Backtest ML gate vs historical POTD ML picks (POTD 2-4 last 6, see [[project_potd_audit_queued_607]])
- v1.1 real L7/L14 wRC+ from FanGraphs ([[project_v11_recency_wrc]])
- Barrel signal audit (once n≥20)
- Attribution backtest wire-up ([[project_attribution_backtest_608]] — needs recency + inverse check first)
- Server-side Jerry reads + prompt move ([[project_jerry_server_side]])

## Pricing model at launch
- **Freemium** — POTD + Dawg of the Day free (trust builders)
- **$12.99/mo paid** — PRIME props pipeline as paywall (the 64% lifetime edge is what people pay for)
- **Not $30+/mo** — sharps benchmark ROI; no public track record yet to justify that tier

**Next action (2026-07-03 AM):** Start RLS Tier 1 batch — audit app INSERT/UPDATE/DELETE calls, write migration file, apply `cohort_display_config` + `prompt_templates` first, verify app + cron 24h, then Tier 2.
