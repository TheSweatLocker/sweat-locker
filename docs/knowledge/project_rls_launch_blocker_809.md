---
name: rls-launch-blocker-809
description: "🚨 LAUNCH BLOCKER: anon key (shipped in app bundle) can INSERT/UPDATE/DELETE all pipeline tables. Confirmed 8/9 via attack test."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-09T20:31:12.322Z
---

**2026-08-09**: Confirmed security exposure that has been pending since 7/17.

**Current RLS state (from `20260717_rls_pipeline_write_hotfix.sql`)**:
- RLS ENABLED on all core tables (Supabase warning resolved)
- BUT `public_write` policy grants anon `FOR ALL TO anon USING (true) WITH CHECK (true)`
- Anon key (shipped in app bundle, extractable) has full INSERT/UPDATE/DELETE
- Verified 8/9: successfully wrote + deleted a test row in `mlb_game_context` using anon key

**Threat model**:
- Any user who extracts the anon key from the app bundle can:
  - INSERT fake `mlb_game_context` rows (skew slate)
  - UPDATE `jerry_reads` (rewrite picks users see)
  - DELETE `mlb_game_results` (break resolver)
  - Overwrite `sweat_card` cache (poison card)
- No audit trail of who wrote what
- No user PII exposure (no user tables in `public` schema — Supabase `auth.users` is protected)

**Blast radius for paid launch**: HIGH data-integrity risk. Financial + reputational damage if a subscriber sees Jerry reading a swapped pick.

**Fix path (proper follow-up already noted in 20260717 migration comment)**:
1. Rotate pipeline to `service_role` key (GitHub Actions secret) — bypasses RLS
2. Drop `public_write` policies; leave only `public_read` for anon
3. App keeps anon (read-only)
4. Should be LAUNCH BLOCKER — bundle with pricing rollout

**Tables currently exposed to anon writes** (per migration):
mlb_game_context, mlb_game_results, mlb_pipeline_props, daily_best_bet_history,
mlb_hr_watch, mlb_umpires, mlb_pitcher_stats, mlb_team_offense, mlb_bullpen_stats,
jerry_cache, daily_dawg, mlb_line_history, prop_edge_calibration,
prop_edge_backtest_history, mlb_tier_calibration

Plus later-added (feature_flags_rls_disable, jerry_rls_disable, prop_jerry_rls_disable
migrations that fully DISABLED RLS to unblock writes).

**Action**: create SERVICE_ROLE_KEY secret in repo → update all pipeline scripts to
prefer it over anon → tighten write policies to service_role only.

**When to do it** (user directive 2026-08-09): bundle with RevenueCat + App Store
subscription wiring. Same session — RLS fix is a pre-req for taking real dollars,
and both touch the "we're a real business now" surface (payments + data integrity).
Estimated combined session: RC/App Store ~1-2 days + RLS rotation ~2 hours.

Related: [[project_launch_priorities_july]], [[project_launch_queue_806]],
[[project_pricing_launch_decision]].
