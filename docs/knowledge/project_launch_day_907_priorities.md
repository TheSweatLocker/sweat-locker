---
name: project-launch-day-907-priorities
description: "🚨 9/7 launch day plan (in order): morning data audit → Steam Room engine audit → NFL parity check → App Store submit"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T04:17:27.561Z
---

**Locked 9/7 evening:** launch day priority order for tomorrow. Grind top to bottom, submit at end if clean.

## Priority order

### 1. Morning data audit (~1-2h)
- MLB: pipeline output sanity check for the day
- NCAAF: same
- Focus on data freshness, missing rows, tier distribution, unusual props
- Not looking to fix bugs — looking to confirm no data disasters landed overnight

### 2. Final Steam Room engine audit (~1h)
- Sharp Card composition — juice caps, tier discipline, publishability gates
- The Ladder — streak state, rung generation, milestone banners rendering
- The Split — sharp vs public data freshness, source coverage
- Ledger — daily P/L calc, teaser builder
- Cross-check all 4 sub-tabs render + refresh correctly on dev build
- Any last-second cleanup before real users touch it

### 3. NFL parity check (~30 min)
- Game reads: verify Jerry per-game NFL reads render structured card
- Takes / picks: NFL primary_play + sharp card entries populate
- Props: NFL Prop Jerry visual now matches MLB (following 9/7 backend fixes)
- Spot-check on dev build with sandbox tester

### 4. App Store submit (~1h)
- Complete ASC submission page (see [[project_asc_submission_page_907]])
- Screenshots + description + keywords + reviewer notes
- Add for Review on both subscriptions
- Click Add for Review on app version → bundled submission
- Manual release (control the launch moment)

## Anything else to consider

Things that could surface tomorrow and delay:
- Apple Small Business Program email — check for approval, flip RC toggle if received
- Cross-sport verify pass on free-tier paywall gates (never completed today)
- Remove any remaining diagnostic logs
- Verify Restore Purchases + Manage Subscription still work in production build
- Confirm ODDS_API_KEY + ANTHROPIC_API_KEY not exposed in any client bundle

## What's already DONE (locked in)
- RC SDK wired, all Pro gates shipped
- Sandbox purchase verified end-to-end (Annual)
- Settings Subscription section (status + Manage + Restore) shipped
- Subscription products in ASC ready
- Bundle ID confirmed
- SBP application submitted (awaiting approval)
- 4 NFL Prop Jerry backend bugs fixed + shipped
- 19,400 rows of 2025 nfl_player_stats backfilled
- NFL prop_jerry_reads went 1 → 155 rows

## Related memories
- [[project_asc_submission_page_907]] — submission page bulk-fill checklist
- [[project_nfl_prop_jerry_needs_work_906]] — pre-launch discussion outcome (executed 9/7)
- [[project_app_store_submission_901]] — original launch target Fri 9/5 or Sat 9/6, slipped to Fri 9/12 or Sat 9/13
- [[project_technical_reference_manual_906]] — Tech Ref Manual scope discussion queued
