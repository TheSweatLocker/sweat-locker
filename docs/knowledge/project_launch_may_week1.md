---
name: app-store-submission-targeted-late-weekend-may-17-18-2026
description: "Launch plan, blockers, day-of-week sequencing"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**STATUS AS OF 2026-07-02: This May target slipped.** New launch plan lives in [[project_launch_priorities_july]] — target mid-August 2026, aligned with NFL v2.0 marketing moment. Keeping this doc as historical record of the original blockers (RevenueCat + Sentry + App Store metadata).

---

**Target App Store submission: tail end of weekend (Sun May 17 or 18, 2026).** Confirmed by Andy on 2026-05-13.

**This week's sequence (today is Wed 2026-05-13):**
- **Wed-Thu (5/13-5/14):** continue model polish, audit, social-content iteration. No big build cuts.
- **Fri (5/15):** RevenueCat + Sentry integration. Both are launch blockers.
- **Sat (5/16):** bug fixes, final TestFlight QA, screenshots / App Store Connect metadata if not already done.
- **Sun (5/17-18):** cut final TestFlight, submit to App Store review.
- Allow 1-3 day Apple review buffer → public launch could land mid-week-of-5/19 or early-week-of-5/26 depending on review time.

**Active launch blockers:**
1. RevenueCat subscription wiring (planned Fri)
2. Sentry crash/error monitoring (planned Fri)
3. App Store Connect metadata, screenshots, privacy disclosures (Sat)
4. QA pass on the current TestFlight build + the queued server-side Jerry changes (see [[project_pending_app_changes]])

**Already in place:**
- Pipeline cron is stable; model props ~242-125 (~66%) on resolved
- Server-side Jerry shipped (game reads + casual_summary tier + parlay/recap template-fetch)
- All pre-launch model bug fixes from this week's audit (BB scorer, ER cluster, get_pitcher_projection Supabase fallback, etc.)

**How to apply:** Don't suggest non-blocking feature work between now and Sunday submission unless explicitly asked. Bug fixes that prevent crashes / data corruption are fine; new feature scope is parked until post-launch. After Friday's RevenueCat + Sentry work, the bar for new code in app/index.tsx should be "would this block submission if it's broken?" — if no, defer.
