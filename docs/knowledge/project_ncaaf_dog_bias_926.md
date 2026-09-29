---
name: project_ncaaf_dog_bias_926
description: NCAAF spread picks on the DOG go 24.3%; on the FAVOURITE 57.9% — and pick drift moves toward the dog
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-26T18:14:37.147Z
---

Andy called this before it was measured: *"feel like model spreads are
always in favor of dawgs for our model consensus in college football and
nfl."*

Measured 2026-09-26 on graded `public_receipts` game_read spread picks:

| sport | pick side | record | hit% | n |
|---|---|---|---|---|
| NCAAF | **DOG (+pts)** | **9-28** | **24.3%** | 37 |
| NCAAF | FAV (−pts) | 62-45 | **57.9%** | 107 |

A 33.6pp gap. Favourites clear the 52.4% breakeven comfortably; dogs are
catastrophic. At n=37 against p=0.524 the dog result is ~p=0.0008, so it
is not noise.

**This compounds with pick drift.** Of the 27 spread picks where the
receipt and live `primary_play` disagreed, **21 moved favourite → dog**
(78%). So a later scoring pass was systematically flipping picks toward
the side that goes 24%. The drift was not neutral churn — it was
destroying edge.

**Why:** the model treats a big spread as value on the dog. On this
season's NCAAF sample the opposite is true, and the FBS-vs-FCS blowouts
(six games at |spread| > 40 on 09-26 alone) are exactly where it reaches
for the dog.

**How to apply:** the DB pick lock shipped 2026-09-26 (`20260926b`) stops
the drift, which by itself preserves the favourite-side picks that win.
Beyond that, `feedback_fade_not_suppress_803` says a sub-45% bucket
should FADE rather than suppress — here that means taking the favourite
where the model wants the dog. Do NOT act on this without a second
season's sample or a holdout; one season of NCAAF is thin, and the
mechanism (does the model pick dogs only in spots it believes are
special?) has not been isolated. Related: [[project_ats_streak_patterns_902]]
which rejected ATS form on 20k games, and
[[project_ncaaf_3wk_calibration_920]].
