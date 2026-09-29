---
name: project_margin_under_projection_926
description: "Both NFL and NCAAF models systematically project the favourite below market, which mechanically manufactures dog value on most games"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-26T21:44:11.919Z
---

Measured 2026-09-26 on unplayed games only, so **leak-free** (both the
projection and the closing line are pre-game; no outcomes involved):

| sport | under-projects the favourite | mean shortfall | model/market magnitude |
|---|---|---|---|
| NFL   | 14/16  (88%) | +1.4 pts | 0.66 |
| NCAAF | 42/55  (76%) | +3.3 pts | 0.76 |

A model that disagrees with the market in ONE direction on 76-88% of
games is not finding value, it is miscalibrated. Every such game yields
`edge = proj - market` pointing at the dog, which is the mechanism behind
the dog record.

Graded outcomes, two independent classification methods agreeing:
- NCAAF favourites 72-48 (60%, n=120) / dogs 15-32 (32%, n=47)   [label-sign]
- NCAAF favourites 50-40 (56%, n=90)  / dogs  8-22 (27%, n=30)   [game_id join]
- Grading itself audited: 104 spread receipts agree, 0 disagree
- Dogs cover ~50% in every spread band historically (n=6,188), so the
  market is efficient and our SELECTION is the broken part

**NFL cannot be judged on outcomes**: only ~15 graded side picks exist
(5-7 favs, 0-3 dogs). NFL side receipts are also inconsistently shaped —
some rows carry `pick_side` with `matchup` NULL, others the reverse — so
any join requiring both returns zero. See [[project_public_receipts_integrity_918]].

ONE CAUSE FOUND AND FIXED (2743cce5): 20 FCS schools were inheriting a
similarly-named FBS school's SP+ because the suffix-strip guard only
consulted the season index it was searching, and prior-season indexes
lack FCS teams. Indiana State carried Indiana's +32.4. That produced the
30-40 pt fake edges on FBS-vs-FCS games.

STILL OPEN: the residual 76%/88% one-sidedness on legitimate FBS-vs-FBS
and NFL games. Do NOT "fix" it by recalibrating K_PTS_SP against
historical outcomes — that fit is leaky, see
[[project_sp_plus_backtests_are_leaky_926]]. The defensible options are
(a) subtract the measured slate-level offset before computing edge, so
disagreements are two-sided, or (b) accept the model is market-centred
and give it real features. This is a model-philosophy call for Andy, not
a silent code change.

Related: [[project_ncaaf_dog_bias_926]].
