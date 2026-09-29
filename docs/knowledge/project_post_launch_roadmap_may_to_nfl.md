---
name: post-launch-roadmap-may-launch-to-nfl-week-1
description: "Frontside vs backside split for what requires Apple submission. Version cadence v1.0 → v2.0 (NFL Week 1, Sept 7). Mostly baseball-only until August with heavy backside iteration."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## Frontside (REQUIRES Apple submission)

- Any change in `app/index.tsx` UI/UX
- New screens, tabs, navigation flows
- Asset changes (icons, splash, fonts, images)
- `app.json` / Expo config / native modules
- Onboarding / permissions / push notification setup
- Adding new sports as visible tabs (NFL prominence in August)
- Subscription products / RevenueCat product IDs / pricing display
- "Bet" / gambling language changes (Apple-sensitive)

## Backside (NO Apple submission needed)

- All scorer logic (`generate_props.py`, scoring algorithms, tier thresholds)
- Cohort audit data + DB-driven calibration
- `prompt_templates` table (Jerry reads, parlay analysis, pick recap)
- Sweat score formula (`play_of_day.py`)
- DOD logic (`generate_dawg_of_day.py`)
- Daily Degen, POTD generation, HR Watch
- Game context builder (`game_context.py`)
- Supabase data (props, results, mlb_pipeline_props, daily_dawg, jerry_cache)
- XGBoost model refresh / retrain
- Cron scheduling

## Version cadence

- **v1.0** — Sunday 5/17-18 submission (today)
- **v1.0.1** — bug-fix release ~1-2 weeks post-launch (Sentry-driven, only if needed)
- **v1.1** — June: UFC polish + HR Watch UI improvements
- **v1.2** — Mid-July: NFL tab teaser + lineup confirmations UI
- **v1.3** — Late July: Trade deadline tooltips + roster-change detection
- **v2.0** — August: NFL Launch (major update, plan for 3-week Apple buffer)
- **v2.1** — Mid-September: NFL Week 1-2 tuning
- **v2.2** — October: Multi-sport peak (sport-switcher critical)

## Monthly focus

### May-June (Baseball-heavy, post-launch monitor)
- Bug-fix v1.0.1 (Sentry-driven)
- ER UNDER → ER OVER scorer pass
- Hits-UNDER PRIME gate retune (demote noise signals)
- XGBoost recency-weighted retrain (Option B from degradation memo)
- NBA Finals coverage (June 5-19)

### July (Calibration deepening)
- NRFI cohort sub-stratification (park-specific, weather-specific)
- Confluence pair-gate experiment
- HR Watch tier validation
- Per-cohort XGBoost models (NRFI specialist, K-prop specialist)
- CART rule discovery for publishable social content

### August (NFL prep + final baseball push)
- Trade deadline = roster churn (see [[user_2026_roster_corrections]])
- NFL Phase 2 audit baselines complete
- NFL model build (1139-game baseline from Phase 1 audit)
- NFL prop scorers (passing, rushing, receiving, TD scorers)
- College football pipeline final pass

### September-October (NFL kickoff)
- NFL Week 1 launch (Sept 7)
- Daily NFL cohort grading
- College football Saturday cards
- MLB playoffs (October) cohort monitoring

## Architectural reasoning

The app is intentionally **thin-client** — UI shells rendering whatever the cron writes to Supabase. That's what enables daily model iteration without binary rebuilds. The smart stuff lives backside.

**Trade-off:** Anytime we add features that REQUIRE UI (NFL launch, new prop types with new display layouts), we pay the Apple-review tax. Plan submissions ahead of those moments.

**Post-launch temptation to avoid:** Adding app-side features. Resist. Keep the app stable. Move smarts backside.

## Submission triggers checklist

Things that REQUIRE Apple submission:
- App icon change
- New sport tab
- Subscription product change (price, tier name, IAP)
- Push notification capability change
- "Bet" / gambling language change (Apple-sensitive)
- New permissions (location, camera, etc.)

## Related
- [[project_launch_may_week1]] — original launch timing
- [[project_nfl_prep_plan]] — NFL Phase 1 baselines
- [[project_nfl_phase1_audit_baselines]] — heavy_home_dog +7 edge
- [[project_jerry_server_side]] — backside Jerry infrastructure (already shipped)
