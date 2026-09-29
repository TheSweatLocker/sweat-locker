---
name: project-mastery-split-by-prop-type
description: "Backside docket — split pitcher_vs_team mastery into K-rate / BAA / ERA dimensions so each prop type fades on the right signal, not a single conflated flag"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Backside docket item (post-launch v1.x): the mastery layer currently treats `pitcher_vs_team_era` and `pitcher_vs_team_avg` as a single conflated "mastery" signal. This causes false-positive fades on K props.

**Trigger case (2026-05-20):** Schlittler PRIME K Over 5.1 (expected 6.6 Ks). Mastery flag: TOR has .400 BAA / 8.10 ERA vs him. I called "skip" on the K prop based on the BAA-mastery flag — but the K Over and the BAA-mastery measure different things. Schlittler can K 7 + give up 5 hits + 4 ER in the same start (K Over cashes, ER Over cashes, BAA-mastery confirmed all at once).

**Why:** The mastery layer was built as a single check ("does this batter group own this pitcher?") but it conflates three independent dimensions:
- `pitcher_vs_team_k_rate` (whiff rate when they do face him) — kills K props if low
- `pitcher_vs_team_baa` (contact damage when they connect) — kills hits-allowed / ER props
- `pitcher_vs_team_era` (runs scored, downstream of both) — kills game side / RL / ML

**How to apply:** When building the v1.x mastery-aware prop scorer:
1. Pull `pitcher_vs_team_k_rate` separately (likely needs a new column from pybaseball/stathead — batter group K% vs this specific pitcher across past meetings)
2. Route mastery flags into the right scorer:
   - `score_pitcher_k_over` / `score_pitcher_k_under` should ONLY downgrade on K-rate mastery
   - `score_pitcher_hits_allowed_under` / `score_pitcher_er_under` should downgrade on BAA mastery
   - `score_dawg_of_day` / `score_play_of_day` (ML/RL/total scorers) should downgrade on ERA mastery
3. Keep the high-level "mastery aligned" / "mastery conflicts" Jerry narrative — but power it from the right dimension per prop

Today's workaround: treat BAA-based mastery flags as informational, not disqualifying, for K props. Surface the conflict in the Jerry read ("Schlittler may give up runs but TOR is a high-whiff matchup specifically") rather than suppressing the prop.

**2026-05-21 audit update:** Verified in generate_props.py — the K, H+A, outs, and walks scorers DO NOT use `pitcher_vs_team_era` or `pitcher_vs_team_avg` at all. Only ER scorers (lines 1568, 1655) consume mastery, and they correctly do so (ER outcomes are most predicted by mastery). **The conflation is not in the code — it was in my verbal analysis (Schlittler 5/20).** No code fix needed for this rule. Future analysts/copy reviewers should still respect the rule when narrating: BAA-mastery ≠ K-mastery, ERA-mastery is for game side / ER prop only. Keeping memory as a discipline guide rather than a code refactor target.

Related: [[project_pitcher_class_projections]] queued IP/ER/Outs projections; same mastery-split logic should flow into Phase B of that project.

Related: [[feedback_verify_pitcher_attribution]] — verify which pitcher the mastery row belongs to BEFORE using it; this fix doesn't help if the attribution is wrong.
