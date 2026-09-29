---
name: app-store-submission-901
description: 9/1 pivot — user targeting App Store submission Fri 9/5 or Sat 9/6. Everything reprioritized to launch-blockers only.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-02T02:03:06.589Z
---

**Target: App Store submission Fri 2026-09-05 or Sat 2026-09-06.**

Between 9/1 and submit date = **3-4 days of build time**. Every decision on features / discussions / backend work must be re-evaluated against this deadline.

**Launch-blocker rubric:**
- ✅ **MUST FIX**: crash bugs, data corruption, misleading text ("Off Rating" showing PPG), blank sections users will report
- ✅ **MUST TEST**: app rebuild end-to-end across all live sports (MLB / NCAAF / NFL) with all cards + no regressions
- ⚠️ **MUST HAVE for review**: legal (privacy policy), screenshots, description copy, subscription flow, TestFlight validation
- ❌ **DEFER post-launch**: new features, backend puller builds not user-visible, strategic discussions, non-critical polish

**🎯 USER DIRECTIVE 2026-09-01 (refined):**
"We do the revenue cat and paywall, legal subscription stuff LAST right before I submit. I want app to be data rich and ready for all sports before."

**Execution order for the 3-4 build days:**
1. **Days 1-3** — data richness push: fill visible data gaps across MLB/NCAAF/NFL/NBA/NCAAB. Fix any bug or thin surface a reviewer would flag. Previously-deferred infrastructure ships are IN-SCOPE if they close a visible UX gap (e.g. NBA four-factors puller — was deferred, now in-scope because it visibly enriches NBA Team Stats card).
2. **Last 12-24 hours before submit** — RevenueCat integration, paywall UI, legal/subscription copy, screenshots, TestFlight final validation. Single focused block.

**Reframe on deferred items:**
Items deferred earlier because "not launch-blocking" now DO count if they add visible data richness. Re-evaluate case-by-case:
- NBA four-factors + pace puller → now IN-SCOPE (visible NBA Team Stats richness)
- NBA historical abbrev backfill → IN-SCOPE if it makes NBA Recent Schedule visibly complete
- NHL pipeline → still DEFERRED (Oct season, no data yet — reviewer won't see NHL games)
- Strategic discussions (Jerry philosophy, Split vision) → still DEFERRED (not user-visible)

**Explicitly deferred (post-launch):**
- NBA historical abbrev backfill (not user-facing critical)
- NBA four-factors + pace puller (current PPG-based data acceptable interim)
- NHL nhl_pipeline.yml + season data (NHL doesn't start until Oct anyway)
- Jerry take philosophy discussion (Thu-lock — strategic, not launch-blocking)
- The Split vision discussion (already gated per-sport, works)
- Sweat Card multi-sport blend design (works for MLB current, football fills naturally)
- Cross-sport game card badges design (works for MLB, NFL/NCAAF get Jerry via 411f9785)

**Still worth executing pre-submit ONLY if quick:**
- NFL prop status verification (if NFL is live and reviewers open it, empty prop card = bad look)
- Any bug surfaced during app rebuild testing
- Content/copy tweaks visible to reviewers

**How to apply going forward:**
- Every "should we do X" question → ask "does this ship risk regression?" and "is this visible to Apple reviewer?"
- YES to both = launch-blocker, ship
- NO to either = defer to post-submission
- User's inner doubt about product quality ([[project_launch_priorities_july]], [[project_launch_queue_806]]) is real — better to submit with fewer working features than more broken ones
- No new backend infrastructure builds unless they close a visible UX gap
- Bias toward TESTING what's shipped rather than shipping new

**Related:**
- [[project_launch_priorities_july]] — mid-Aug 2026 launch (was original target)
- [[project_launch_queue_806]] — full pre-launch queue + user weighing paid launch timing
- [[project_pricing_launch_decision]] — $14.99/mo + $119.99/yr + 7d trial
- [[project_rls_resolved_818]] — RLS blocker resolved
