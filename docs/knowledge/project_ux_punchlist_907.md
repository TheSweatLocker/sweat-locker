---
name: project-ux-punchlist-907
description: "🎯 9/7 UX punchlist: 5 items surfaced during launch-prep audit — weekly-sport date labels, switch timing, Receipts on paywall bullet, NCAAF/NFL card sparseness, analysis-timing hint"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T15:05:02.445Z
---

**Queued 9/7 during grader audit + launch prep:** 5 UX/design items to close tomorrow. All non-launch-blocking but visible during first-open on device.

## 1. Weekly-sport game cards need day + time (not just time)

**Problem:** NFL / NCAAF game cards on the Games tab show ONLY the time (e.g., "8:20 PM ET"). The tab-header says "TODAY · 17 GAMES" but if a game is 6 days out it feels ambiguous.

**Fix:** For sports with `tab_scope='weekly'` (NFL / NCAAF / UFC), format game card times as `Sat · 8:20 PM ET` OR `Thu 9/11 · 8:20 PM ET`. For daily sports (MLB / NBA / NHL / NCAAB) keep just the time since all games are today or tomorrow.

**Where:** Games tab game-card render in [app/index.tsx](app/index.tsx). Find the `commence_time` formatter and add sport-aware branching.

**Also:** The "TODAY" header should say "THIS WEEK" for weekly-scope sports (or similar) to match the tab labels users see.

## 2. Nail down NFL/NCAAF week switch timing

**User asked:** when does the app switch from "This Week" to "Next Week"? Currently the bucketing is:
- NFL: today → today+10 days = "This Week", today+11 → today+17 = "Next Week"
- NCAAF: today → today+7 = "This Week", today+8 → today+14 = "Next Week"

**Better convention** (to eliminate confusion):
- NFL: switches Tuesday morning after last week's MNF ends
- NCAAF: switches Sunday night after last week's Sat slate wraps
- Or use season_week from game_context tables to bucket "This Week = current NFL/NCAAF Week number, Next Week = current+1"

**Fix:** Long-term proper fix is to use `nfl_game_context.season_week` / `ncaaf_game_context.season_week` for bucketing (referenced in prior week-bucketing commit 72fe59c6). For launch, add a small subtitle under the "This Week / Next Week" chips: e.g. `Week 1 (thru Mon 9/15)` and `Week 2 (Tue 9/16 →)`.

## 3. Paywall preview — add "Receipts on every sport" bullet

**Problem:** The Jerry is Pro Feature paywall preview lists Prop Jerry, Daily Degen, Dawg of the Day, Per-play WHY panels — but NO mention of Receipts as a Pro benefit. Users don't know they get graded records tracking as part of Pro.

**Fix:** Add a bullet to the paywall preview cards (Jerry tab, Steam Room tab, Game Detail bulk gate — all use the same [PaywallPreview](app/index.tsx) component):
- "Receipts tracked on every sport · every pick graded nightly"

**Where:** Jerry preview at [app/index.tsx](app/index.tsx) around line 14285-14295 (`bullets={[...]}` array).

## 4. NCAAF + NFL Games tab cards look bare (before clicking in)

**Problem:** User feels game cards for NCAAF/NFL are bare on the Games tab — no signal indicators visible before tapping into detail. MLB cards feel more populated (probably from the ML/RL/Total tri-line rendering + Vault badges).

**Fix candidates:**
- Add tier chip (🔒 PRO PICK for free users, real tier for Pro) — already done in paywall gate work, verify it renders for NCAAF/NFL
- Add sport-agnostic "handicappers %" chip if externals loaded
- Add sport-specific badges: NFL should show `spread · total · ML` triangle same as MLB; NCAAF the same
- Verify the Sweat Score chip renders for NCAAF/NFL when available

**Where:** Game card render loop in Games tab, [app/index.tsx](app/index.tsx). Compare MLB render vs NFL/NCAAF branches to find what's missing.

## 5. Sport-aware "when to view" hint at top of Games tab

**Problem:** New users don't know when NFL/NCAAF picks refresh. If they open Games tab NCAAF on Tuesday, they might see stale Week-2-not-yet-generated cards and think product is broken.

**Fix:** Add a subtle top-of-tab note per sport. NOT phrased as "picks drop at X" (too tout-y). Better framing:

- **NFL** (weekly): `Full analysis populates by Thursday morning ahead of week's kickoff`
- **NCAAF** (weekly): `Full analysis populates by Wednesday for Thu-Sat slates`
- **MLB / NBA / NHL / NCAAB** (daily): no note needed (analysis refreshes every morning)
- **UFC** (event): `Full analysis populates Wednesday of fight week`

**Where:** Games tab, above the game list, sport-aware conditional. Existing `state_message` band from sport_registry could handle this — one row per sport with the copy above. See [project_faq_sport_registry_source_906].

## Sequence tomorrow

Not launch-blockers. If time permits after data audit + Steam Room audit + App Store submission page work, knock these out in this order:
1. Paywall bullet (5 min — one-line array push)
2. NCAAF/NFL cards sparseness — spot-check dev build, one small fix if obvious
3. Weekly sport date labels — 15 min in game card render
4. Sport-aware "when" note — 10 min once we decide phrasing
5. Week switch timing — bigger, may push to v1.0.1

## Related
- [[project_launch_day_907_priorities]] — main launch day plan
- [[project_faq_sport_registry_source_906]] — sport_registry sourcing (state_message field can host the "when to view" hint)
- [[project_nfl_prop_jerry_overnight_status_907]] — NFL Prop Jerry now shipped
