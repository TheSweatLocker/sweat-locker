---
name: project_unified_taxonomy_decision
description: All public-facing pick labels unify to PRIME / STRONG / LEAN (3 tiers). Decision made 2026-06-16. Avoids gambling language (LOCK / BET / EDGE etc) per App Store review + TikTok content rules. Refactor scheduled post-POTD audit.
metadata:
  type: project
---

**Decision (2026-06-16):** All user-facing pick conviction labels unify to **PRIME / STRONG / LEAN**. Three tiers, one vocabulary, across totals + sides + props + parlays + POTD.

**Why these three names:**
- Already in use server-side (prop pipeline + NBA generator) — no new vocabulary
- Editorial/analyst framing (not gambling slang) — passes App Store review
- TikTok content-policy safe — can be used in paid + organic social
- Three tiers max keeps casual-bettor cognitive load low

**Why NOT "LOCK" or similar:**
- App Store review flags gambling-promotional language
- TikTok bans content with terms like "lock," "guaranteed," "bet on this"
- User explicitly steered AWAY from this vocabulary post-original-pivot

**Internal taxonomies stay unchanged** — each engine layer keeps its native vocabulary:
- Resolver: ELITE/STRONG/LEAN/LIGHT (server-internal)
- Cohort engine: PROBATIONARY/STRONG_EDGE/LOCK (server-internal)
- Tier×type: EDGE/CALIBRATED/COINFLIP/FADE (server-internal filter)
- POTD selector: elite/secondary/value/solid (LEGACY — will collapse to PRIME/STRONG/LEAN at emitter)

**Translation layer** sits at server emitters + app render boundary. Server engines unchanged.

**Sweat Score (0-100)** is the unifying number behind every label. Thresholds for PRIME/STRONG/LEAN cutoffs set POST-audit (TBD — user explicitly deferred until POTD audit data is in hand).

**Refactor scope (one atomic PR when scheduled):**
- Server emitters: play_of_day.py, generate_sweat_card.py, signal_resolver.py output mapping, generate_mlb_game_reads.py Jerry struct
- App scrub: app/index.tsx — POTD render (HEADLINE/SECONDARY/BEST AVAILABLE → PRIME/STRONG/LEAN), driver labels, sweat dim labels, all hardcoded legacy strings
- Grading paths: daily_best_bet_history.confidence migration with backfill mapping, resolve_potd.py output
- Tracking: track_live_tier_record.py tier categorization rename
- Social/Jerry templates: prompt_templates table updates for unified vocab

**Sequencing (per user 6/16):**
1. Launch + brand discussion (first)
2. POTD audit (informs thresholds + per-pick-type defaults)
3. Universal taxonomy refactor (atomic PR, full grep, no half-state)

**Hard rule:** when refactor ships, single grep pass across server + app for legacy labels. No partial migration — atomic switch.

Related: [[feedback_brand_tagline]] [[project_launch_may_week1]] [[feedback_no_kenpom_attribution]]
