---
name: project-surface-walkthrough-909
description: "🎯 9/9 product walkthrough — 8 daily surfaces, their unique lens, redundancy killed, ship order. Decisions locked with user."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-09T22:58:12.824Z
---

9/9 walkthrough covering POTD, Dawg of the Day, Daily Degen, The Split,
The Ladder, The Sweat Card, The Sharp (Sharp Card), The Ledger. Decisions
locked with user; ship order defined; user directive: "work through list now."

## Surface-by-surface locked decisions

**1. POTD**
- BUG: reads jerry_reads (LLM narrative, conv max 55-68) instead of
  mlb_game_context.primary_play (real resolved pick, conv 76+ today).
- Passed 2 days in a row while PRIME plays (CLE ML PRIME 85, BOS ML
  PRIME 76) sat available.
- FIX: read from primary_play + include TOP prop from mlb_pipeline_props
  if top conviction + LR-gated. POTD should be able to select a prop as
  the day's headline pick — currently can only pick sides/totals.

**2. Dawg of the Day**
- Currently: ML dogs +150 to +250 with LR ≥ 55% win prob + market
  disagreement ≥ 15pp
- Rec: keep as-is, but add "second Dawg" for full-slate football
  Saturdays + Sundays. One Dawg is thin for a 15-game slate.

**3. Daily Degen**
- Currently: filtered high-conviction props. Doesn't feel "degen."
- REBUILD: 4-10 leg SGPs, +500 minimum payout, no LR gate (LR would
  strip the exact +money legs that make Degen distinctive), per-leg
  odds -125 to +300, ban -200+ juice legs.
- Rationale from user: "leave Daily Degen but let smake more juicy plus
  odds why i agree like 4-10 leggers on hevay game days."

**4. The Split**
- Currently: sharp/public divergence surface, no dedicated record-keeping
- FIX: record outcomes in daily_surface_records like every other tab
- ENHANCE: merge Anti-Public INTO The Split as sub-lens (not separate
  surface — keeps UI surface count flat)
  - Sharp Divergence sub-lens: handle% > bets% by 15+pp
  - Anti-Public sub-lens: bets% ≥ 70% on one side AND model + sharps
    on OTHER side
  - Triple Confirmed chip: all 3 sources (cz/fr/oc) agree
- Both sub-lenses tracked in same records bucket ('split' or split into
  'split_sharp' + 'split_antipublic')

**5. The Ladder**
- Currently: MLB only, single play/day, rolling stake, props supported
- ROLLOUT plan:
  MLB   ✅ live
  NFL   Wk 2 start (Thu 9/11)
  NCAAF Wk 2 start (Sat 9/13)
  NHL   Season opener Oct 7
  NBA   Season opener Oct 21
  NCAAB Nov 3
  UFC   Fight nights only (existing)
- Props already supported (user clarified — I confused earlier).

**6. The Sweat Card**
- Currently: home page hero, top_8 + lock + POTD + dawg merged
- ENHANCE: add "Yesterday X-Y" widget at top of home → clickable to
  Receipts. Scroll down further → yesterday's actual Sweat Card plays
  with results. Filed for v1.0.1.

**7. The Sharp (Sharp Card / Steam Room)**
- Currently: 15-30 PRIME+STRONG plays across ML/spread/total/props,
  conviction 65+, LR-gated, FADE-blocked
- FIX 1: enforce 1-of-each-market floor per day — minimum 1 side + 1
  total + 1 prop even if convictions thin. Quality floor per market
  rather than top-N regardless of type.
- FIX 2 (NEW): rolling 7-day cold-streak auto-tighten. If 7-day hit
  rate < 50% (sample ≥ 15 plays), raise conviction floor 65→70 AND
  cap daily volume at 15 (from ~25). Auto-normalizes when 7d rolling
  climbs back above 55%. Display "🥶 Cold streak — tightening filters"
  banner during cold-mode. Real product moat vs competitors that
  publish same volume regardless of form.
- FIX 3 (deferred): expand sharp_flow category to include The Split
  plays in surface_records (previously proposed; deferred until Split
  records land).

**8. The Ledger**
- Currently: 2-4 leg SGPs + teasers from Sharp Card picks at published lines
- ENHANCE: chalk-mode variant. Take PRIME player prop at 43 rush yds →
  auto-generate correlated alt-line 30 yds version as parlay anchor.
  For NCAAB, expect wallpaper of juiced MLs + player alts.
- Split into two variants:
  Chalk-mode:      2-4 legs, +150 to +400, -200 to -110 per leg
  Published-mode:  2-4 legs, +200 to +600, -125 to +110 per leg (current)
- Ledger STAYS chalkier; Degen owns +money chase.

## Product-direction principles

**Kill redundancy → each surface owns a UNIQUE lens:**
- POTD:      "Highest-conviction single play"
- Dawg:      "Best +money price — market wrong on price"
- Degen:     "Longshot parlays only — high variance, chase payoff"
- Split:     "Follow the money OR fade the herd"
- Ladder:    "Streak-chaser — one play, rolling stake"
- Sweat:     "Curated 8-play best-of"
- Sharp:     "Everything PRIME/STRONG — power user 25-play deck"
- Ledger:    "Chalk parlays + teasers only"

**Efficiency wins (post-launch refactor):**
- Compute plays ONCE in a shared "picks pool" — each surface FILTERS the
  pool with its unique lens. Kills duplicate compute + drift between
  surfaces where same play shows different convictions.
- Grade at pool level, roll up to surface. Prevents same play from
  double-counting in POTD + Sweat + Sharp records.

## Ship order (in progress)

1. Fix POTD source bug (immediate, biggest visible impact)
2. Sharp Card 1-of-each-market floor
3. The Split outcome recording
4. Sharp cold-streak auto-tightening
5. The Split anti-public sub-lens
6. Ledger chalk-mode variant
7. Daily Degen SGP rebuild
8. Ladder all-sport config rollout
