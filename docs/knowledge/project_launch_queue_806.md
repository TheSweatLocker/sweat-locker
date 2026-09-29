---
name: project-launch-queue-806
description: "Prioritized pre-launch queue as of 2026-08-06. User weighing paid launch timing — this is what's between \"founder trusts picks\" and \"willing to pay strangers trust picks.\""
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-11T01:25:40.149Z
---

Comprehensive queue after tonight's launch-readiness conversation. User feels the product is close but not ready for paid launch — this memo captures why and what's queued.

**Shipped tonight** (2026-08-06):
- Persona shift + translation layer (Jerry as analyst, bettor English)
- Hallucination guards A/B/C (auto-retry + name whitelist + substitution)
- Sweeper line-matching fix (Bohm-style wrong-line odds cleaned)
- POTD team-name label uniformity ("Boston Red Sox ML" not "Home ML")
- MC HIGH-CONF juice-band gate + ha_over fair-price fade

**EXECUTION ORDER — 2026-08-09 reshuffle** (starting now, work backwards from Sept 4 NFL kickoff):

**START-NOW batch** (Aug 10-30 · execute in this order — dependency-ordered):
1. **Grader coverage audit + fix** (blocker for #2 and Should-have #1). 100% of published picks auto-graded; found Sonny Gray BB Over gap 8/5. Also: UFC grader (no result columns per [[project-ufc-high-conf-sweep-809]]).
2. **Receipts tab rework** — sport-agnostic, Jerry actual pick record across sports (depends on #1 clean grader). Bundle with public track record page.
3. **Cross-prop consistency validator** (immediate Prop Jerry bug — Javier ks_under FADE vs outs_under BACK on same pitcher).
4. **Sport-tab contamination audit** — Tomorrow banner leak fixed 8/9; sweep rest of app for MLB-copy leaking into other sport tabs.
5. **Prop Jerry multi-sport readiness** — verify NFL/UFC prop routes end-to-end.
6. **UFC card redesign** — match MLB card quality.
7. **LEAN/READ tier UI styling** in app.
8. **"Data as of X" freshness timestamp** on card.
9. **Number-hallucination hard-enforce** (retry still hallucinates → stronger enforcement or deterministic templating).
10. **Onboarding (3-screen)** + FAQ.

**HIGH-PRIORITY BUG QUEUE — from 8/10 slate audit** (fix before more slates ship):
- **Jerry stat-hallucination hardening** (2026-08-10 user callout): Jerry's synth prose keeps citing specific numbers that don't match reality. Confirmed today:
    * "Kremer 11.57 first-inning ERA" — didn't verify against struct (probably real but Jerry stated as fact without traceable source)
    * "Wesneski 8.83 ERA" — was "vs SF historical" but Jerry stated as current L3 (actual L3: 4.02)
    * "Cameron 4.41 xERA getting rocked" — Cameron L3 ERA is 0.39 (elite), opposite of Jerry's story. Struct data may be stale season xERA vs recent form
    * "Christian Scott 1.3 IP opener last outing" — fully hallucinated, Scott is a 5-inning starter
    * "Taillon 10.22 L3 ERA" — actual 10.41, off by 0.19 (close but not verified)
  Root cause: `validate_jerry_read.validate()` checks numeric CITATIONS against struct but doesn't verify the SEMANTIC accuracy of the surrounding claim ("getting rocked" when data says elite). Also lets through numbers that ARE in struct but represent DIFFERENT metrics than Jerry claims.
  Fix scope:
    1. Stat-attribution mapping — every numeric claim in prose must trace to a specific `struct.pitcher.<field>` with correct metric interpretation. If Jerry says "L3 ERA X.XX" the number must match `struct.away_pitcher.l3_era`, not `struct.away_pitcher.season_era` or `struct.away_pitcher.era_vs_opp_historical`.
    2. Directional check — if Jerry says "getting rocked / dominant / shelled" the surrounding number must actually support that read. Cite Cameron xERA 4.41 while claiming rocked → check L3 ERA which is 0.39 → contradiction → block.
    3. Struct freshness gate — reject synthesis if struct pitcher stats are >48hr stale (Cameron problem may be old struct).
    4. Post-synth grep against MLB API — pull actual L3 stats for cited pitcher via statsapi, verify Jerry's claim within tolerance.
  Add to `jerry_pre_publish_audit.py` as critical checks.

**NEAR-LAUNCH bundle** (Aug 31 - Sept 3 · do together in one focused session):
- RevenueCat + App Store subscription wiring
- RLS rotation to service_role ([[project-rls-launch-blocker-809]], ~2hr)
- Legal disclaimer (state-specific, 21+, entertainment only)
- Support channel (email minimum)
- **Website update** (2026-08-09 user directive) — landing page for pricing, feature marketing, sign-up funnel. Bundled with RC/App Store because it's the "we're a real business now" surface.

**Sept 4** — NFL kickoff — LIVE.

**POST-LAUNCH sport parity** (in this order):
- **NCAAB** by Nov 3 (small lift — framework exists, cron already wired)
- **NHL** by Oct 8 (medium lift — fresh build, small scope: FREE NHL API + MoneyPuck)
- **NBA** by Oct 21 (largest lift — offseason rebuild + prop pipeline)

**Should-have** (would help retention):
1. Public track record page (auto-updated, no cherry-picking)
2. "How to read the card" 3-screen onboarding — SHIPPED 8/9 (index.tsx tier hierarchy walkthrough)
3. FAQ (what's PRIME/STRONG/LEAN/READ, how to bet)
4. ~~NFL Jerry structured output~~ — **SHIPPED 8/9** (Phase 2). Dual-write path in generate_nfl_game_reads.upsert_read() → upsert_jerry_read_nfl() at line 477. Parser handles SHORT/LONG/CALL blocks. Cron wired in nfl_pipeline.yml. Just 0 rows currently because no NFL games in 8-day window (preseason gap). Auto-fires when Aug 15+ preseason resumes + heavily from Sept 4.

**High-leverage intelligence upgrades** (user requested queue):
- **A. Live line-movement detection** — compare consecutive line_poller snapshots to detect reverse-line-move (public 70% on side A but line moves toward side B = sharp money). ~4 hours.
- **B. Time-weighted historical priors** — exponential decay weighting on bucket_roi (14d × 3, 30d × 1.5, 90d × 1). Sharpens trap detection ~5-10pp. ~3 hours.
- **D. Ensemble stacking** — bayesian model averaging weighted by recent per-model hit rate. Infra exists in [[project-adaptive-model-ensemble-802]]. ~2 days.
- **H. Multi-sport pipeline consolidation** — unified schema + calibration across MLB/NFL/NCAAB/NCAAF/NBA. 2-3 weeks but permanent payoff.
- **I. Public vs sharp market microstructure** — track WHICH books move first (Pinnacle/Circa = sharp leaders). Requires book-level line tracking. 3-5 days.

**Low-leverage but user-approved**:
- Cross-sport universal fade pattern transfer (patterns proven in MLB → NFL/NBA hypotheses)
- Weather × park factor interaction (currently separate)
- Umpire-specific priors (have `umpire` field, don't use heavily)
- Custom filters/notifications (product feature)
- Historical similar-game search ("what happened in similar spots")

**Sport parity gaps** (user directive: start ASAP):
- MLB: 95% ready
- NFL: 50% (game reads write prose to jerry_cache, NOT structured jerry_reads — Phase 2 needed)
- NCAAB: 35% (framework exists, no Jerry integration)
- NCAAF: 35% (same)
- NBA: 20% (offseason rebuild in progress)
- UFC: 60% (partial Jerry, no calibration)

**Launch options user is weighing**:
- A: Free/founder-friend beta 30 days → paid Sep 1
- B: Paid beta at $4.99 for 90d, reset to $14.99 after Sep 30
- C: Full $14.99 launch now with 4-6 week polish push

Recommended: A or B, not C. Charging $14.99 while 5/6 sports aren't Jerry-driven promises what we can't deliver.

Related: [[project-calibration-architecture-805]], [[project-nfl-launch-readiness-806]], [[project-launch-priorities-july]]
