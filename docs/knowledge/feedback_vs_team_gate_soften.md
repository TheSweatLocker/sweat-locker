---
name: feedback-vs-team-gate-soften
description: "vs-team mastery signals: surface small-sample data (n<15 IP) but weight it lightly — don't fully suppress, don't let it dominate"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-22T01:41:58.110Z
---

**Rule:** For `pitcher_vs_team` signals (ERA/BAA/K per 9), the current 15-IP hard-cutoff gate at [game_context.py:1291](../../../SweatShop/mlb_pipeline/game_context.py#L1291) is too binary. Persist the number regardless of sample size and let downstream scorers apply a sample-size weight rather than treating anything below 15 IP as null.

**Why:** 8/21 Cameron vs DET example — 11 IP career, .158 BAA, K/9 ~9.82. Gate suppressed the K/9 entirely. Numbers exist for a reason. User said: "more recent trends should be weighed and mastery shouldn't be dominating weight, but it should be viewed nonetheless." Complete suppression makes us blind to a real (if small-sample) signal.

**How to apply:**
- When touching vs-team logic: prefer `weight = min(1.0, ip_vs_team / 15.0)` style scaling over binary null gates.
- Scorers/Jerry that cite vs-team stats: attach the n_starts / IP alongside the number so readers see the sample and can discount.
- The original 15-IP gate was added post-Matz-incident (2026-05-27) to prevent one hot outing masquerading as "mastery." Keep that protection for the MASTERY LABEL/prose (don't call n=2 "dominant vs this team"), but expose the raw number as evidence — not as gospel.
- The recent-3-starts function (`get_pitcher_vs_team_recent`) is already tolerant of small samples; make the 5-season aggregate consistent by removing the hard-null and moving to weighted output.

**Related:** [[feedback_verify_pitcher_attribution]], [[project_signal_framework_821]], [[feedback_sample_size_with_pct]].
