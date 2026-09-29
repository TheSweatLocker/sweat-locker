---
name: project-potd-selection-redesign-908
description: "🎯 9/8 queued: POTD selection redesign discussion. Option A (LR>=0.60 gate) shipped tonight. Bigger discussion: should props be POTD-eligible? Rank formula (sweat_score vs LR vs signal_confluence)?"
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T17:58:56.292Z
---

**User directive · 9/8 evening:** Ship Option A (LR>=0.60 gate) now, queue up a broader POTD selection discussion. Plus: **should props be POTD-eligible?** Currently POTD is game-only (sides/totals/ml).

## The core product principle driving this

User explicitly stated 9/8: **"It is vital to the product to have stats-backed analysis. Stats and analysis need to be honest and accurate."**

POTD is the app's most prominent single pick. If POTD is a coin-flip pick with sweat_score-inflated conviction and Claude-confabulated narrative, we're violating the north star. Any POTD selection change must serve this principle.

## Option A shipped tonight (9/8)

Post-eligibility LR-confidence gate in `jerry_anchor_potd.py`:
- After the juice gate, fetch primary_play._lr_p_home_win / _lr_p_over per eligible pick
- Compute LR-support for the pick side (p_home if HOME pick, 1-p_home if AWAY)
- Drop picks with LR support < 0.60
- Sports without LR wired pass through unchanged (backwards compat)

Effect on today's slate: Phillies ML (LR p_home=0.57 → 0.57 support for HOME) would have been dropped. Next-best pick with LR ≥ 0.60 would surface instead.

## Bigger discussion queued for tomorrow

### 1. Prop POTD eligibility

Currently POTD selector reads `jerry_reads` (game-only). Props live in `prop_jerry_reads` and are ineligible. Why props might be a great POTD:
- Prop hit rates are often higher than game hit rates (single-player performance ceiling < team performance ceiling)
- LR-endorsed props hit 62-73% (per 14-day audit) vs games ~55-60%
- Prop conviction is more precisely gradable

Why current design excludes props:
- Props require live starter (game-day skip risk — postponed rain-out kills a game read too but affects fewer users)
- Props are surface-specific (Sharp Card, Steam Room) — POTD as "cross-surface highest conviction" would compete with those surfaces
- Prop reads use different schema (render_sections not narrative)

**Decision points:**
- (a) POTD stays game-only (status quo)
- (b) POTD becomes cross-surface: eligible picks include jerry_reads + top-N prop_jerry_reads (STRONG+ with LR support > 0.65 + high refit conviction)
- (c) Add "Prop of the Day" as a separate surface alongside POTD — one game pick + one prop pick per day
- (d) POTD includes ONE prop only when no game pick clears the LR gate (fallback)

### 2. Rank formula redesign

Current rank: sweat_score (composite that inflates conviction). Alternatives:
- LR probability directly (0.60 = LEAN, 0.65 = STRONG, 0.70 = PRIME)
- Signal_confluence_net weighted (need 3+ signals agreeing)
- Blended: 0.5 * LR + 0.3 * signal_confluence + 0.2 * sweat_score
- Hard rules stack: LR ≥ 0.60 AND conv ≥ 65 AND signal_confluence net > 0

### 3. Narrative honesty enforcement

Even with tighter selection, some picks will have mixed signals. When they do, narratives must acknowledge dissent rather than confabulate. Currently:
- generate_potd_narrative.py sends limited context to Claude
- Claude reaches for whatever number is present (often wrong market)
- 9/8 fix: market-aware prompt + honesty rules (shipped)
- Remaining risk: Claude still hallucinates specific numbers

**Options:**
- Send Claude a full signal-confluence breakdown table (which lens votes which side)
- Require Claude to state signal count ("3 of 4 signals argue X, 1 argues Y")
- Human-in-the-loop review for POTD narratives before publish (manual QA before go-live)
- Kill Claude for POTD, use template with slot-filled specifics from ctx (deterministic + trivially accurate)

### 4. POTD grading + backtest transparency

Once POTD selector is redesigned:
- Backtest the new rules against 30-90d of graded picks
- Measure: hit rate, ROI, dissent (how often signal-confluence agreed with the pick)
- Publish backtest to Receipts so users see the selector's true edge

## Related work
- [[project_lr_totals_investigation_908]] — cross-sport LR audit
- [[project_data_infrastructure_priorities_908]] — Priority 1 (LR gates)
- [[project_lr_shadow_promotion_907]] — LR shadow signal spec (paralleled by tonight's Sharp Card gate)
- [[feedback_smart_fades_809]] — smart fades pattern when pipeline overrides own sim
- generate_potd_narrative.py — current narrative build
- jerry_anchor_potd.py — POTD selector (tonight's Option A landed here)
