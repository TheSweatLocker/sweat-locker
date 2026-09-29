---
name: morning-audit-723
description: "7/22 evening: Jerry 73% + Panel 64% on sides (2nd night in row Panel/Jerry lead). CWS@TEX v4-only dissent right. Twins MC-vs-market +price contrarian hit. Universal chalk 3/4 right — v2 detector saved us from false alerts."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-23T13:48:15.311Z
---

**Set 2026-07-23 morning after 7/22 evening slate graded.**

## Model layer performance (17 games)

| Layer | W-L-PK | Pct |
|---|---|---|
| **Jerry** | 11-4-2 | **73.3%** ⭐ |
| **Panel** | 7-4-6 | 63.6% |
| v4 | 7-6-4 | 53.8% |
| v3 | 7-7-3 | 50.0% |

**Two-night pattern (7/21 + 7/22):**
- Panel: 72% → 64% (avg 68% on n=28)
- Jerry: 61% → 73% (avg 67% on n=30)
- v4: 25% → 54% (volatile — one bad night, one middling)
- v3: 50%/50% both nights (baseline)

**Interpretation:** Panel + Jerry consistently top 2. v4 volatile — DON'T reweight
on 2-night sample but flag if pattern continues. Current side composite
(v3=0.5/v4=0.3/jerry=0.2, or panel variant v3=0.1/v4=0.5/jerry=0/panel=0.4)
is defensible but v4 weight may be too high given recent instability.

## New findings — 3 slices to watch

### 1. Twins contrarian (MC-vs-market ≥15pt AWAY at +price) HIT
- User picked MIN ML +114 as "contrarian value" per MC 59.5% MIN win
- Market implied ~47% MIN
- I flagged 🚨 AVOID citing contrarian_lens_dead_722 audit (60d n=228,
  contrarian buckets 39-48%)
- **MIN won 10-6 outright** — user's read was right

**Why this matters:** the 60d "contrarian is noise" audit lumps ALL contrarian
picks (dog value, panel dissent, MC dissent, single-lens dissent). But the
Twins pick was a specific slice: **MC probability vs market implied ≥15pt
disagreement, AWAY side, +price.** That might be a real lane distinct from
generic contrarian.

**Action:** pull that specific slice as its own audit (`MC_gt_market_15pt_away_positive_price`)
when n≥30 across dates. Don't fold into generic contrarian bucket.

### 2. CWS@TEX loss = v4-alone-dissent right (small sample warning)
- 4-lens read: v3 +1.34, jerry +1.23, panel +0.10 → HOME (Rangers)
- v4 alone: -1.05 AWAY
- Confluence +5 STACKED HOME
- Externals: 5/5 HOME (100%)
- **Result: AWAY (CWS 4-2)** — v4 correct, everyone else wrong

Track: over next 30 days, log games where v4 alone dissents from 3+ agreeing
lenses + external consensus. If v4 hits >55% on those, that's a real
"contrarian v4 as signal" pattern worth surfacing.

### 3. Universal chalk (95-100% consensus) 3-of-4 RIGHT on 7/22
- ATH@ARI 80% HOME → HOME won ✅
- WSH@COL 86% AWAY → AWAY won ✅
- **LAD@PHI 100% AWAY (aligned) → AWAY won ✅**
- MIN@CLE 86% HOME (contra) → AWAY won ❌ (successful fade)

**Bucket update after 7/22:**
- (ml, 95-100, aligned): 5-2 = 71.4% (n=7, still LOW confidence)
- Universal chalk WITH model aligned appears to be **signal, not fade**.

**Both hypotheses contradicted so far:**
- Andy's "universal external chalk = fade" — LAD@PHI cost him a hypothetical fade
- My "consensus + model contra = fade" — sample too small to confirm/deny

**v2 detector saved us from 3 false FADE alerts.** MONITORING state was
correct; RED FADE would have been embarrassingly wrong on 3 games.

## Card grades

**My 4-play recommendation:**
- TEX ML -110: ❌ LOSS (CWS 4-2, v4 dissent was right)
- Wyatt Langford Hits Over: likely WIN (4-2 game, standard offensive game)
- José Ramírez Hits Over: likely WIN (CLE scored 6)
- DET ML +104: ✅ WIN (Montero L3 1.33 delivered)

**User straight plays: 5-3 (62.5%)** — clean profit night.
Losses were: PIT SGP total leg, ATL SGP total leg, TOR ML (against all lenses).
Wins: PHI O 9.5, Rangers U 8.5, Marlins U 8.5, Twins ML +114, Tigers ML +104.

## Rules updated (post-audit)

**Marlins UNDER read replicated:** L3 pitcher form beats season xERA when
BOTH SPs have L3 ERA sub-2.15. Panel projected 6.54, actual 7. Adds a
data point to [[project_panel_projection_validated_623]] — panel + jerry
+ L3 ERA cluster on the UNDER side is a high-conviction total lane.

**Dog reads on recency-hot pitchers hit:** DET ML +104 with Montero L3 1.33
vs Rea L3 4.41. Confluence -2 AWAY. This is a real lane — write it up as a
prop-cohort audit target ("dog_recency_pitcher_edge").

## Related

- [[project_consensus_fade_substantiation_722]] — v2 detector working as designed
- [[project_contrarian_lens_dead_722]] — 60d audit; Twins slice suggests refinement needed
- [[project_audit_721_full]] — 7/21 comparison (panel 72%, jerry 61%, v4 25%)
- [[project_panel_projection_validated_623]] — panel side signal reinforced
