---
name: slate-grading-728
description: Full 7/28 MLB slate artifact locked for EOD/next-morning grading. First full day with oddscrowd bets/money divergence data — special interest in whether div ≥+10pp sharp side aligned with model consensus actually hit.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-28T22:58:10.292Z
---

**Artifact:** https://claude.ai/code/artifact/5107a7ef-ad4e-48b5-aaef-c5b315dafdf0
**Slate date:** 2026-07-28 · 16 games
**Snapshot type:** Full per-game breakdown at close — every lens (Panel/Jerry/v3/v4/MC/Conf), model score prediction (avg + per-model), externals (docsports/pickswise/action/vsin/pickdawgz/bettingpros/covers), oddscrowd bets%/money% per market, pipeline verdict, top props, pitchers + park + weather footer.

**Why:** First full day with oddscrowd wired in. We need to dissect:
  1. Which lens combinations actually hit vs missed
  2. Did oddscrowd sharp side (div ≥+10pp) align with winners more than model consensus alone
  3. When lens + externals + sharp money all triple-aligned (BOS, WSH, CIN today), did that stack print
  4. Anti-consensus fade tier fires on ARI@PIT — did it work
  5. Which of the 39 PRIME+STRONG+LEAN props hit; correlated-stack risk (5 Nats props / 6 Padres props)
  6. Whether SD -231 juice trap warning was right (heavy home fav pattern)

**How to apply:** Morning 7/29 grading pass:
  1. `python mlb_pipeline/grade_daily_card.py --date 2026-07-28` — grade primary picks
  2. Pull mlb_game_results for 7/28, join to snapshot data in [[project_ml_rl_and_new_tiers_727]]
  3. For each game in the artifact, mark: (a) which lens picks won ML, (b) whether total went over/under vs consensus, (c) oddscrowd sharp side w/l, (d) triple-alignment plays w/l, (e) prop hit rate by tier
  4. Update [[project_daily_degen_tracking_live_726]] baseline if DD legs fired
  5. Write EOD grade memo — if oddscrowd sharp shows ≥ +5pt lift over lens-alone at n≥10, promote to scorer input consideration

**Related:**
- [[project_ml_rl_and_new_tiers_727]] — new tiers on trial (ML consensus fallback, anti-consensus fade)
- [[feedback_ml_vs_rl_conflation]] — permanent rule the artifact respects (columns split ML vs total; no cross-labeling)
- [[project_external_transparency_differentiator]] — this artifact is a preview of what graded externals look like
- [[feedback_full_slate_artifact_format]] — Full slate everything = HTML artifact (this)
