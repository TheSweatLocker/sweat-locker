---
name: project-home-screen-hot-streak-907
description: "🎯 9/7: dynamic home-screen hot-streak banner + adaptive record header + gold-styled admin note (3 UX ideas)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T15:13:13.225Z
---

**Queued 9/7 evening:** three related home-screen UX ideas to surface product wins to users who might not be paying attention. Rich feature — worth doing right.

## 1. Adaptive record header (top of Home)

**Current state:** Hardcoded "MLB 59% L30D" at top of home. Breaks when:
- MLB goes off-season (Oct 31)
- Another sport is doing better (NCAAF up 20u while MLB is flat)
- Multi-sport combined figure is more accurate

**Design options — pick or combine:**

**A. Rotate to the sport that's currently in-season + hot**
- Priority: (in-season) > (last 7d hit%) > tiebreak by units
- Renders "NFL 68% L30D" during Sept-Feb, "NBA 61% L30D" during Oct-Apr, etc.
- Simple, matches user expectation

**B. "All Sports" combined view**
- Roll up every sport → "Overall 62% L30D · +47.5u"
- One consistent number no matter the season
- Loses per-sport granularity but never drifts stale

**C. Show top 2-3 sports (small chips)**
- `NFL 68% · MLB 55% · NCAAF 62%` in a row
- Best of both — visible per-sport but not one hardcoded champion
- More screen real estate

**Recommended: C with A as fallback if only one sport has enough sample.** Chip row scales with what's in-season without an arbitrary "primary" pick. Post-launch we could add tap-to-drill-down.

**Data path:** already have `surface_records` keyed by (sport, surface, window_key). Just need to filter to sports with `sport_registry.state = 'in_season'` and rank by units_net (or hit_pct).

## 2. Home-screen "Hot Streak" banner (dynamic, DB-driven)

**Concept:** dynamic 1-line banner at top of Home surfacing what's currently hot across surfaces. Rotates through:
- "🔥 The Sharp is hot — 21-5 last 3 days, +19.4u"
- "🔥 Daily Degen has hit 2 in a row — going for 3 tonight"
- "🪜 The Ladder pushing step 5 — 4.8u ride active"
- "📈 Ledger 3-day green streak — +5.2u"
- "🏈 NFL Week 1: 8-3 (73%), +6.2u"
- "🐕 Dawg of the Day hit at +180 last night"

**Why it matters:** casual users who only tap into Game Detail miss the aggregate wins. A user might see one MLB pick lose and churn without knowing the Sharp is up 20u over the last 2 days. This banner is the retention loop — it makes users feel like the whole product is working, not just the one pick they saw.

**Design:**
- 1 line at very top of Home tab, above POTD hero
- Non-tappable OR tappable → deep-links to the surface (e.g. Ladder banner opens Steam Room → Ladder)
- Rotates every 6-8 seconds if multiple are eligible
- Gold accent color for text + subtle warm-tint background (analogous to admin note but hotter)
- Silent hide when nothing meets threshold (no "here's a random stat" filler)

**Backend:** new nightly job `compute_home_streak_banners.py` that:
- Reads `surface_records[d3]` (need to add d3 window if not exists) or `daily_surface_records` last 3 days
- Ranks all surfaces × sports by "hot" score = (win_pct × 0.4) + (units_net × 0.3) + (streak_length × 0.3)
- Writes top 3-5 banners to a new `home_streak_banners` table with pre-formatted copy
- App reads latest N rows on Home mount

**Templating:** copy is composed server-side (not client-side) so we can control voice. Template shape:
```
{icon} {surface_display_name} {is_verb} {qualifier} — {stat_summary}
```

**Threshold gates:**
- Sharp: 3+ day window with 60%+ hit rate AND +5u+ net → surface
- Daily Degen: 2+ consecutive wins → surface
- Ladder: active streak of 3+ steps → surface
- Ledger: 3+ consecutive positive days → surface
- Per-sport: 65%+ hit rate over 7 days with 10+ picks → surface

If nothing meets threshold on a given day → silent hide, no banner.

**Cost/complexity:** medium. Backend job + one table + templating logic + Home render. Post-launch v1.1 feature — not launch-critical but high user-value.

## 3. Admin note styling — make it POP

**Current:** grey letters on dark background = invisible. User "already have gold available (accent)."

**Fix (tomorrow, 5 min):**
- Text color: `THEME.accent` (gold) instead of `THEME.textDim`
- Background: `THEME.accent + '14'` (10% tint) for subtle warmth
- Border-left: `THEME.accent` 3px accent stripe
- Optional: small icon prefix like ⚠️ or ℹ️ to draw the eye

**Where:** find current admin note render in [app/index.tsx](app/index.tsx) — likely uses `admin_note` field from `sport_registry` or similar config table. Grep for the exact copy the user's referring to.

**Also worth doing:** if we build the hot-streak banner (item 2), it uses the SAME styling pattern (gold accent) so the app develops a visual language:
- Gold + hot: something is currently working well (streak banner)
- Gold + info: important message from us (admin note)
- Gold + warn: data issue (rarely used)

## Priority sequence

Not launch-blockers. Rank for post-launch work:
1. **Admin note gold styling** — 5 min, ship tomorrow if we have time
2. **Adaptive record header (option C)** — half-day, post-launch v1.0.1
3. **Hot-streak banner** — 1-2 days total (backend job + table + frontend + copy templating), post-launch v1.1

## Related memories
- [[project_launch_day_907_priorities]] — main launch plan
- [[project_ux_punchlist_907]] — other UX items (paywall bullet, card sparseness, etc)
- [[project_sharp_card_composite_record]] — Sharp Card record composition
