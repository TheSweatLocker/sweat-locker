---
name: project-may18-v4-day1-audit
description: "5/18 v4 day-1 production results, Daily Degen rewrite validation, cohort confirmations"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

5/18 was v4 XGBoost runs model's first full production day. 14 games resolved. Day-one results filed for trend tracking.

**v4 vs v3 totals (5/18 only):** v3 = 12/14 (85.7%), v4 = 5/12 (41.7%). One-day variance — backtest was 62.4% — but worth noting: v4's strong leans at Δ≥1.5 went 2/2 (n=2), while marginal v4 leans were noisier than v3's blended stats anchor. Read: trust v4 strong-conviction only, treat marginal v4 leans as noise.

**POTD ATH/LAA NRFI 93 cashed** — NRFI 90-94 sweet spot now at 11-5 (68.8%) 30d / 22-9 (71.0%) lifetime. Cohort still loudest single audit signal.

**PRIME confluence ML hit 2-0** on day. 30d window improved 62.2% → 64.1% after the good day. Yankees ML -177 at PRIME +5 cashed (was the borderline-EV pick I incorrectly walked back during 5/18 card revision).

**Daily Degen 5/18 was 3-of-4** — only miss was Royals ML LEAN +1 (KC lost). The new cohort-aware Daily Degen rewrite (committed 1404f75 5/18) would have replaced that LEAN leg with Imai outs UNDER STRONG (which cashed). So the rewrite went 4/4 hypothetically on day 1. Validation of the per-cohort prop audit overhaul.

**Cohort confirmations from 5/18:**
- outs_under STRONG: 3-0 (Imai, Rojas, Corbin all hit) — matches 91.7% L30d / 10-0 lifetime
- hits_over STRONG: 5-1 (83.3%) — exceeds 69.2% baseline
- hits_over PRIME: 4-2 (66.7%) — slightly under 77.3% baseline, normal variance
- hits_under STRONG: 2-4 (33%) — cold day, watch for cohort drift
- ha_under PRIME: 0-2 — small sample but bad day

**Why:** Production day-1 sanity check on the v4 model + Daily Degen rewrite + sweat-tier gate + YRFI gate. Multiple structural changes shipped 5/17-5/18; needed to confirm none of them broke the core signal pipeline.

**How to apply:** When auditing later production days, compare against this baseline. If v4 strong leans (Δ≥1.5) start hitting below 60% on n≥10, that's the trigger to investigate. If outs_under STRONG drops below 80% on n≥10, investigate the cohort key match. Link: [[project-v4-xgboost-spread-model-priority]]
