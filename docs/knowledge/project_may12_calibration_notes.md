---
name: 5-12-calibration-findings-confluence-7-loss-houser-split
description: "Two findings from 5/12 worth tracking at the 5/17 audit checkpoint — confluence_extreme_ge6 cohort dented, Houser-style fragile-starter cluster split outs-under ✓ / ER-over ✗."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**5/12 results review:** Lotto parlay 1-7 (one ungraded). Deep-dive 4-game tracked set: **4-6-2** (40% W/L). Pipeline season prop record still ~242-125 / 66% after the day; no aggregate-level break, but two specific findings to revisit.

**Finding 1 — SF/LAD +7 confluence blew up.**
- Cohort: `confluence_extreme_ge6` was 5-2 STD (audit table) before today; this game (Dodgers ML/RL/Houser-ER all PRIME-rated) lost three correlated picks. Cohort now ~5-3 STD.
- It's still n<10 so single-game variance dominates, but flag for the 5/17 audit: if it drops below 60% on n≥10, the "+6+ confluence stacks PRIME" framing needs a tier downgrade.
- **Why:** [[project_xgboost_spread_model_priority]] — XGBoost informational spread that day was +0.11 vs v3 +2.61 — XGBoost disagreed sharply. When the two models diverge, +7 confluence is less reliable than the headline number suggests.
- **How to apply:** Don't strip the cohort yet (n too small), but at 5/17 if confluence_extreme_ge6 is <60% on n≥10 AND XGBoost-v3 spread divergence is ≥2 runs, add a "XGB-divergence" downgrade gate that drops PRIME → STRONG.

**Finding 2 — Houser-style fragile-starter cluster split.**
- Houser outs UNDER 14.5 ✅ (he got hooked early per thesis); Houser ER OVER 2.5 ❌ (limited damage before exit). Same starter, same signal cluster, opposite results.
- **Why:** outs-under cashes whenever the starter is short for ANY reason (pull, blowout, weather, pitch count). ER-over needs damage to compound BEFORE the exit. Two different probability surfaces.
- **How to apply:** When a fragile-starter cluster fires (L3 ERA ≥6.0 + 1st-inn ERA ≥6.0 + xERA ≥5.0), prefer outs-under as the *primary* expression. Treat ER-over as a *secondary* gate-by AND opp lineup wRC+ ≥110 AND park ≥100 (so the damage has somewhere to land). Codify before next sprint review.

**Validated on 5/12 (keep using):**
- Gallen outs UNDER 14.5 ✓ — same fragile-starter cluster, primary-expression form, cashed clean
- Saggese hits UNDER (7 straight hitless, .000 L7) ✓ — high-floor low-juice slot bet
- SEA/HOU OVER 9.0 (+3 vs market) ✓ — strongest pre-game total edge cashed
- PRIME hits-OVER stack (Wood, Brady House, etc.) — multiple winners; tier remains calibrated
