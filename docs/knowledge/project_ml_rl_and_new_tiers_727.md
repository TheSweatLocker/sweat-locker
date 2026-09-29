---
name: ml-rl-and-new-tiers-727
description: "7/27 EOD — 13 commits shipped. ML/RL conflation bug fixed (v2 render + memory rule). Two new scorer tiers: ML consensus fallback (5+/6 lens) + anti-consensus fade (MC+1 alone vs 3 dissenters, 74% inverse). UFC pipeline back live. Launch blockers narrowed."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-28T02:59:25.784Z
---

**Set 2026-07-27 EOD after brutal 0-3 public card night that produced 13 defensive commits.**

## What broke publicly today
Public DET ML pick was mispitched as "6/6 lens confluence lock." The 6/6 was **spread-cover** math (DET +1.5), not ML math (only 4/6 lens had DET winning outright). Followers took DET ML at -120 thinking every model agreed. Structural fixes:
- `_slate_analyze_v2.py` — separate `_ml_picks` and `_rl_picks` fields
- `_slate_render_v2.py` — three columns (WINS_ML / COVERS_SPREAD / TOTAL) + DIVERGE banner
- `feedback_ml_vs_rl_conflation.md` — permanent memory rule

## New scorer tiers shipped
Both live in game_context.compute_primary_play as of 7/27 EOD.

**1. ML consensus fallback** (`e46ae789`)
Fires when 5+/6 lens agree on ML direction but no lens has big enough spread delta to trigger the existing delta-gated tiers. Backed by 30d audit: 5/6 lens agreement hits 69% (n=13). Gate: skip if winning-side ML ≤ -220 (juice trap).

**2. Anti-consensus fade** (`4660c3bc`)
Fires when MC agrees with exactly ONE stat lens (Panel/Jerry/v3/v4) and the OTHER 3 stat lens all pick the opposite side. Historical: MC+Panel alone = 10% win rate (fade wins 90%); other combos aggregate 26% (fade wins 74%). Tier:
  - MC+Panel alone → STRONG fade (n=10, 90%)
  - MC+other alone → LEAN fade (n=25 combined, ~68%)

Fires BEFORE MC-HC check so even a MC-HC 92% call can be a fade if the pattern matches. Practice: rarely overrides MC-HC because MC-HC usually has 3+ lens partners.

## Real lifetime numbers (post-pagination fix `28765e6d`)
Was showing 987 samples truncated to 1000-row PostgREST cap. Real numbers:
- **PRIME: 763-449 · 63.0% (n=1,212)**
- **STRONG: 794-532 · 59.9% (n=1,326)**
- **LEAN: 263-205 · 56.2% (n=468)**
- **OVERALL: 1,820-1,186 · 60.5% (n=3,006)**

Use these numbers in the receipt post + all future "here's the record" copy.

## UFC pipeline BACK
Was silent since 5/21 due to UFCStats.com bot-check. Now:
- `ufc_card_scraper_v3.py` uses ESPN scoreboard API
- Scorer produces real predictions (4 PRIME + 3 STRONG on Aug 1 Belgrade card after accent-lookup fix)
- `pull_externals_ufc.py` scaffold + BFO parser live (10 picks for Aug 1)
- First live validation: Aug 1 UFC Belgrade card

## Launch blockers as of 7/27 EOD (mid-Aug target)
1. **TestFlight rebuild** — main is 2+ weeks ahead of last user build; all 13 today's commits + prior weeks invisible until this happens
2. **In-app Track Record surface** — daily_grades table exists but no dedicated tab explaining pipeline + rolling numbers
3. **UFC first live test** — Aug 1 Belgrade needs to score + resolve cleanly before mid-Aug launch
4. **Cold-streak content playbook** — need receipt post + "what we're doing about it" template ready for future losing weeks

## Content ready
Receipt post drafted (methodology + 3006-sample record + football tease + fix list). Will be posted tomorrow as pinned post. Uses CORRECTED numbers (60.5% n=3006, not the truncated 987 that showed pre-pagination-fix).

## Tomorrow's docket (in order)
1. Morning grader run → audit tonight's 0-3 card
2. Finalize + publish receipt post (pin it)
3. TestFlight rebuild + submit
4. RevenueCat + App Store Connect (revisit prior blockers per user)
5. Website + ToS + Privacy — add external picks disclosure language
6. Monitor anti-consensus + ML consensus tiers as they fire (14-day validation window)

## Related
- [[feedback_ml_vs_rl_conflation]] — permanent rule
- [[project_daily_degen_tracking_live_726]] — DD tracking baseline
- [[project_ufc_pipeline_broken_726]] — closed today
- [[project_launch_priorities_july]] — mid-Aug target still tracks
