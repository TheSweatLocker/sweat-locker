---
name: project-may31-outcome
description: 5/31 public card 4-1 (NYY/Brewers/Miller/MIN-PIT Over win; Miso K loss by 1) — first night of sweat-dim rescore + same-day prop pipeline fix shipped
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

5/31 public card outcome: **4-1**.

- ✓ Yankees ML (PRIME 88 SIDE — confluence 9 + Warren mastery vs OAK + Jerry +5.37). NYY dropped **13 runs in the 3rd alone** vs Athletics. Loud directional read.
- ✓ Brewers -1.5 RL (POTD). Won **2-0**. Model projected +2.3 margin; printed the receipt at the tightest possible cover.
- ✓ Bryce Miller U 1.5 BB (PRIME 76 ✓book — only book-verified PRIME of the night).
- ✓ MIN @ PIT Over 7.5 (TOTAL STRONG 75 — all three models OVER, contrarian to NRFI 93).
- ✗ Misiorowski O 8.5 K (STRONG 75 ✓book — pitched **7 IP, 8 K**, 1 short of the line). Variance loss, not process loss.

**Supplementary read worth flagging in the morning audit:**
- SF/COL Giants hits stack (7 PRIME hits-overs, all ⚠no_book) — every batter in the lineup got at least one hit. The signal was right; only the Phase 2 book-attach rail kept it off the public card. Vindicates the prop scorer and underlines why the [[project_pm_cron_live_game_prop_overwrite]] fix matters for surfacing these stacks as card-grade in the future.

**Context — what shipped tonight alongside the 4-1:**
- Sweat dim scorer rewrite (commit 59581a4 — Jerry/drift/trap-zone rescue). First live night collapsed 7 PASS games to 1, surfaced NYY ML as PRIME 88. Day-1 in production validated by NYY hitting.
- Props pipeline fixes (commit cf9909c — tier-aware top_n cap + live-game preserve guard). Same-day fix for the BB prop dropout + live-game overwrite trust-killer that nearly cost us tonight's Miller and Bradish picks.

**Streak:** third straight winning night per user. Two consecutive 4-1 days (5/30 + 5/31).

**Tomorrow morning audit docket:**
- Full per-leg breakdown vs new sweat-dim scores
- First-day calibration check on Jerry SIDE / TOTAL drivers (NYY landing PRIME is a positive data point; need to look at the LIGHT_LEAN games that didn't surface plays too)
- SF/COL hits-stack post-mortem — would Phase 2 attach + live-game preserve have surfaced those publicly?
- Misiorowski 7 IP / 8 K outcome — does the K projection model need a 7-IP-deep adjustment?

**Related:** [[project_sweat_dim_jerry_drift_531]], [[project_pm_cron_live_game_prop_overwrite]], [[project_may30_potd_bugs_and_gap]], [[feedback_no_nrfi_on_cards]]
