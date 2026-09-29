---
name: project-lr-shadow-promotion-907
description: "🎯 9/7 learning: LR cross-market shadow signals should be promotable to alternative picks. MIN@DET 9/7 case — LR shadow OVER STRONG hit; pipeline picked ML COVERAGE lost."
metadata:
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T22:21:41.921Z
---

**Real-world learning · 2026-09-07 · MIN @ DET post-game.**

## The case

**Game outcome:** DET 5, MIN 4 · Total 9 (OVER 8.0) · DET wins ML

**Pipeline surface:**
- Primary play: MIN ML [COVERAGE conv 97] → LOST (DET won)
- LR overlay on ML: NULL (feature coverage gap, no p_home_win score)
- **LR total shadow: OVER STRONG p_over=.61** → WOULD HAVE WON

**Model stack agreement on OVER (all three):**
- V3 EPA-matchup total: 10.2
- Monte Carlo total: 10.56
- LR shadow: 61% OVER STRONG

**What the pipeline picked instead:** MIN ML (COVERAGE tier), no LR support, lost.

## Root cause

LR cross-market shadow signals (`primary_play._lr_ml_shadow`, `primary_play._lr_total_shadow`) currently live as METADATA only. They're computed and stored but:

1. **The pick engine doesn't consider them for tier assignment** — a strong shadow signal on the market pipeline didn't pick doesn't influence which market surfaces.
2. **Jerry can cite them in prose** (via `pre_parsed_facts.total_market_delta`) but users see them buried inside the read, not as a "here's an alternative pick" chip.
3. **Sharp Card composer doesn't include LR-shadow-endorsed cross-market plays.**

**Rule that should exist:** when the pipeline's primary_play is COVERAGE-tier OR has no LR ML support, AND the LR shadow on the OTHER market is STRONG (p ≥ .60 or p ≤ .40), promote the shadow pick to an alternative surfacing option.

## Fix design

**Phase 1 (post-launch, small ship):** app-level surface
- In Game Detail, when `_lr_total_shadow.suggested_tier == 'STRONG'` AND primary_play is a different market, render a small chip: **"🎯 LR SHADOW: OVER STRONG (61%) — cross-market"**. Tap deep-links to prop/parlay logging with the shadow pick pre-filled.
- No ensemble math changes. Just surfaces the metadata that exists.

**Phase 2 (medium ship):** ensemble scorer integration
- Modify `ensemble_scorer._score_market` to accept LR shadow input.
- When primary market's tier is COVERAGE/LEAN AND shadow has STRONG conviction:
  - Option A: promote shadow market to primary_play, demote current primary to `_secondary_play`
  - Option B: emit both primary_play and secondary_play; app renders both with equal weight
- Prefer Option B (preserves the existing primary_play contract; adds secondary as new field).

**Phase 3 (larger):** Sharp Card composer includes LR-shadow-endorsed cross-market plays
- Add rule: if `_lr_total_shadow.suggested_tier == 'STRONG'` AND the game isn't already on Sharp Card via primary_play → include shadow as a distinct Sharp Card item.

## Blast radius (how often does this fire)

Rough audit — 10 MLB games today, LR shadow output distribution:
- STRONG conviction on shadow market (would fire alternative surface): **~3 games/day**
- MODERATE (p between .55-.60): ~2 games/day
- Weak/neutral: ~5 games/day

3 alternative picks/day × 55%-target hit rate = meaningful additional edge that's currently invisible.

## Why this matters

The LR overlay was built as an INDEPENDENT signal — it's supposed to catch what the ensemble misses. When it strongly disagrees with the ensemble on WHICH MARKET to play, that disagreement is a feature (independent perspective), not a bug to ignore.

Today's MIN @ DET is the canary. LR + V3 + MC all agreed OVER; pipeline picked ML COVERAGE that had no LR support at all; DET wins ML AND total goes OVER; user sees a losing ML pick when a winning OVER was in the metadata the whole time.

## Related
- [[project_predictive_signals_backlog_907]] — Wk 3-4 item "LR keep/drop decision based on hit-rate lift" pairs with this
- [[project_nfl_model_pipeline_discussion_907]] — LR overlay is a key model layer
- [[docs/NFL_MODEL_PIPELINE_AUDIT.md]] — LR live status confirmed for NFL + MLB
