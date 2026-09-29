---
name: project-model-correlation-watch-803
description: Watching whether unanimous-model consensus picks cluster losses; n too small to conclude
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-03T23:52:12.719Z
---

**Observation** (2026-08-03, do NOT act on yet): Two consecutive days of headline losses on picks where Panel + Jerry + v4 + MC all agreed:

- **8/2 Brewers ML -232 POTD**: MC 83% MIL win. Angels shut them out 0-3.
- **8/3 Yankees ML/UNDER (Schlittler POTD anchor)**: MC 72% NYY win, all 3 models unanimous UNDER. Schlittler gave up 3 R / 3 BB in 1.1 IP → likely loses ML + UNDER + Schlittler O Ks prop.

**Hypothesis**: When Panel + Jerry + v4 + MC all agree, they may share the same underlying assumptions (L3 xERA, historical BAA, K-rate matchup). "3 models unanimous" isn't 3× confidence — it may be 1 signal counted 3 times. When the shared input is wrong (starter has a bad day), all 3 models are wrong together — variance is concentrated instead of diversified.

**Why: NOT saved as an actionable rule (yet)**:
- n=2 days is nothing. Yesterday Jerry went 8-4-1P (72.7%) overall despite the Brewers loss.
- Historical unanimous-model calls have real edge. Downweighting them based on 2 days is outcome bias / overfitting to variance.
- The response would produce false negatives for months (missing legitimate locks) to avoid the rare 2σ blowup.

**How to apply** (only after n≥30 unanimous-consensus calls tracked separately):
- If unanimous 4-model consensus hits BELOW 60% at n=30+, revisit
- If unanimous stays 65-70%, this observation was noise, delete this memory
- Do NOT act on the pattern yet; process discipline > reactive prompt patches

**What's already accounted for** (patterns saved earlier that DID have n backing):
- [[feedback_heavy_fav_ml_trap_803]]: -200+ ML trap (n=8 60d + qualitative data)
- [[feedback_sharp_money_discipline_802]]: sharp $ discipline (multi-signal audit)
- [[feedback_batter_hits_juice_trap_803]]: batter hits juice trap (n=17+ PRIME rate data)
- [[project_confluence_net3_trap_729]]: net=±3 trap zone (n=90+)

**Monitoring plan**:
- Add "unanimous_4model" flag to jerry_reads audit reports
- Track hit rate by (unanimous vs disagreed) split over 30 days
- Only then decide whether prompt needs adjustment

Related: [[project_composite_debias_finding_712]] (Jerry debias itself was based on real audit data, not vibes), [[feedback_dont_fade_prime_on_pattern_alone]] (don't override the pipeline based on short-term pattern).
