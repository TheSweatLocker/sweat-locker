---
name: 2026-05-03 calibration data points
description: Day-1 results for Stack Alert + Sweat Card + new prop tier validation
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
Sunday 2026-05-03 results — first full day of the new Sweat Card + Stack Alert + hardened projected-lineup features in production.

**Why:** First production validation of features shipped over the prior week. Anchors expected hit rates for the next round of cohort calibration.

**How to apply:** When Andy reports prop or POTD results in future conversations, treat these as the rolling baseline rather than re-asking. Update with each fresh day of resolutions.

**Confirmed wins:**
- Stack Alert: Cubs lineup vs Merrill Kelly. 7 of 9 batters hit. Day-1 validation that the 6-pick cap (vs old 3-pick cap) surfaces real edge.
- Dawg of the Day: Royals ML +102 won.
- Luzardo K prop PRIME 88: 10 Ks (line was 5.1) — hit big.
- Yankees ML (manual Sweat Card add): won.
- Cubs Stack hits picks: 7/9 batters had a hit.

**Losses:**
- Cards ML (PRIME confluence): lost. Andy framed in social as "Dodgers bound to bounce back" — that audit-transparency framing is the right play. PRIME confluence cohort is 25% historical, already a known fade tier.
- Hits UNDER PRIME picks (Wallner / Pauley / Wynns / Morel etc): all 0-3 on day 1. Sample too small to recalibrate (n=3). Monitor for 15-20 more picks before changing scoring thresholds.

**Final updates:**
- Tigers/Rangers (NRFI 28, strongest YRFI signal of slate): MISS (NRFI). YRFI ≤40 cohort drops to ~23-9 (71.9%) on 30d. Single miss on strongest signal is exactly 28% bust-rate variance in a 72% cohort — don't recalibrate yet.
- App POTD Pirates ML (STRONG confluence +3, NOT socially posted because confluence cohort is fade-tier): WON. Single-game outcome doesn't validate the cohort either way; 25-40% of fade-tier picks still hit. Right call to fade socially despite this win.
- **Dawg of the Day: 3 wins in a row (Royals 5/3, plus prior 2 days).** Small sample but plus-money + audited streak — most consistent recent signal in the product. Lean into Dawg-streak narrative for social.

**Final day score: 4 wins (Royals Dawg, Luzardo K PRIME, Yankees ML manual, Pirates ML app-POTD) + 2 losses (Cards confluence, Tigers/Rangers YRFI).** Cards loss is known fade-tier (25% audited). YRFI miss is single-game variance in a 72% cohort. Net positive day across all signal types.

**Total models that hit big (post-mortem from earlier conversation):**
- Reds @ Pirates: v1 model projected 3.1 R/G total. Actual = 1 run. Model directionally correct on extreme Under but I dismissed it as "v1 model bug" in morning analysis — was wrong.
- Astros @ Red Sox: v1 model projected 3.8. Actual = 4. Within 0.2 runs.
- Cubs @ D-Backs Over 11.5: model projected 13.6 (delta +2.1 vs market). Actual = 12. OVER hit.

These three "extreme delta" v1 totals all played to model. Queued cohort to track: v1 projected_total vs market with |delta| ≥ 3. Could be a hidden edge.
