---
name: project_ufc_model_fades_lose_925
description: UFC winner model only works when it agrees with the favourite; every fade loses 2-of-3
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-25T22:53:57.883Z
---

Measured 2026-09-25 on `ufc_picks` (n=96 with winner_actual + recommended_side):

| split | record | rate |
|---|---|---|
| model overall | 51-45 | 53.1% |
| always-favourite baseline (same fights) | 46-20 | **69.7%** |
| model AGREES with favourite (n=36) | 26-10 | 72.2% |
| model FADES favourite (n=30) | 10-20 | **33.3%** |

The model contributes nothing. Its wins come entirely from agreeing with
the market, where it merely matches the always-favourite baseline. Every
time it disagrees it is wrong two thirds of the time — worse than a coin
flip, and n=30 at 33.3% is significant against p=0.5 (~0.049) let alone
against the 69.7% baseline.

`pick_result` on the graded subset is 25W-38L = 39.7%, which is what
would feed a published record.

**Why:** an underperforming model is one thing; a model that is
anti-predictive exactly where it adds information is another. Fading the
favourite is the only situation where the model says something the market
doesn't, and that is precisely its failure mode.

**How to apply:** do not publish UFC winner picks that fade the market
favourite. Either gate to fav-agreement (honest but it is just chalk) or
run UFC as data/analysis only until the model beats the baseline. Related:
distance + method already suppressed via SUPPRESS_DISTANCE (UFC distance
was 56.4% vs a 67% always-no baseline). See [[project_vault_ctx_depth_starves_patterns_925]]
for the same "measure against the right baseline" lesson.
