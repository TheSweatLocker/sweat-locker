---
name: feedback-lr-daily-verify
description: "Morning audit MUST verify LR is producing distinct per-game predictions, not stale/identical fallback values"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-11T03:06:33.397Z
---

**Rule:** Every morning audit MUST include an LR liveness check for MLB (and NFL/NCAAF when in-season). Verify each game's `primary_play._lr_ml_shadow.p_home_win` and `_lr_total_shadow.p_over` are DISTINCT per game — not the same value across the whole slate.

**Why:** LR (supervised logreg) has been performing well when it runs. But 2026-09-10 audit found LR shadow showed identical `p_home=0.4076 / p_over=0.4656` for every MLB game on the slate — a fallback/stale value, not real per-game predictions. Discovering stale on the NIGHT OF picks means the LR gate never actually ran on any pick that day. Users lost the LR signal without knowing. Related: [[project_lr_shadow_stale_909]] (LR runs pre-close-lines, stores stale shadow) — but on 9/10 it wasn't even stale from earlier, it was fallback constants meaning the compute path failed entirely.

**How to apply:** In morning audit script (and any daily readiness check), pull today's primary_play._lr_ml_shadow values across all MLB games. If ≥3 games share the same p_home_win to 4 decimal places, flag as BROKEN and alert. Same check for _lr_total_shadow.p_over. Should run BEFORE picks publish, not after.

**Follow-ups:**
- Debug why 9/10 LR was returning identical values (features look present in ctx — issue is likely in the compute path or model file)
- Fix pipeline so LR always runs post-close-lines
- Add monitor/alerting when LR is stale
