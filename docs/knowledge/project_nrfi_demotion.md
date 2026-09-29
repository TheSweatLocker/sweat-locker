---
name: nrfi-demotion
description: NRFI demoted from primary_play / POTD slot 5/30 after MIA/NYM POTD loss confirmed PRIME NRFI 90-94 = 50% coinflip. NRFI now lives in supplementary_play with companion-signal gate.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

NRFI is no longer eligible for primary_play or POTD designation as of 2026-05-30. ML and totals lead headline plays; NRFI surfaces as `supplementary_play` ("Also worth a look") only when (a) it's in a meaningful score band AND (b) a companion signal is present.

**Trigger:** 5/29 MIA/NYM Marlins/Mets POTD NRFI lost. Audit data already confirmed bare NRFI is coinflip: PRIME NRFI 90-94 cohort = 50% on n=22 / 30d. We posted as POTD anyway. User decision 5/30: stop promoting NRFI as headline because POTD must mean edge.

**Companion signals** (any one promotes NRFI from LEAN supplementary to STRONG supplementary):
- Ace duel: both starters ≤3.0 xERA
- Cold weather: ≤55°F
- Pitcher park: ≤95 run factor
- NRFI-friendly umpire (zone tag with "under" in note)

**Files changed 5/30:**
- [`mlb_pipeline/play_of_day.py`](mlb_pipeline/play_of_day.py)
  - `_compute_supplementary_play(ctx)` — new helper returning NRFI/YRFI struct or None
  - `score_mlb_game()` writes `dimensions.supplementary_play` alongside `model_play`
  - NRFI sweat contributions halved: sweet-spot was +30 → now +15, 88-89 was +22 → +11, etc.
  - PRIME `primary_play` floor narrowed: covers `ml/spread/rl/over/under/total` only (nrfi/yrfi excluded — they shouldn't auto-elevate)
  - `total_play` no longer falls through to NRFI band label
  - **New direction-conflict gate**: if `total_delta` and `prop_dir` disagree AND prop_dir has 3+ aligned PRIME/STRONG, suppress total_delta contribution. Triggered by MIA/NYM where v3 said OVER +1.9 but 4 Meyer pitcher-suppression props pointed UNDER.
- [`mlb_pipeline/game_context.py`](mlb_pipeline/game_context.py)
  - `compute_primary_play()` PRIME NRFI 90-94 block REMOVED (was line 1971-1981)
  - NRFI no longer trumps STRONG ML/STRONG total in the priority chain
  - YRFI STRONG 6-8 1st-inn ERA block kept (different cohort, audit ~63%)

**Dry-run delta on 5/29 slate** (what *would* have happened with new code):
- MIA/NYM: PRIME 98 NRFI headline → **STRONG 79 "Over 7.5" headline + NRFI STRONG supplementary**. NRFI moves to supplementary. ⚠️ Initial draft had this as "Under 7.5" via prop confluence — user caught it on 5/30. Reality: Meyer was abysmal tonight, OVER hit. The "Under" call would have lost too. See "Correlated prop stack lesson" below for the fix.
- LAA/TB: NRFI 89 surfaces as LEAN supplementary (no companion) — transparency tag only, doesn't drive sweat.
- All other games unchanged (no NRFI in their breakdown).

**Correlated prop stack lesson (5/30 catch):**
Same-pitcher prop stacks aren't independent signals — they're one bet wearing multiple hats. MIA/NYM 5/29 had 4 Meyer Unders (HA / ER / Outs / etc.) all bets on Meyer pitching well. Counted as 4 separate prop confirmations they triggered a direction-override that would have flipped the headline to Under. Meyer then got shelled — OVER hit. Lesson cost: would have been a public loss.

Fixes shipped same evening:
1. `_compute_prop_alignment` dedupes by `player_name` — each player counts once for their highest-tier directional vote, not once per prop type.
2. Direction-conflict suppression threshold raised from 3 → 4 distinct PLAYERS. At 2-3 aligned players prop_dir adds to sub-score (+14 / +6) but doesn't override model direction. Only 4+ aligned distinct players overrides.
3. Same threshold (4+) applies to prop_dir-as-total_play resolver.

Result: MIA/NYM 5/29 → **OVER 7.5** headline (which hit), Meyer Unders count as 1 player not 4, NRFI stays supplementary.

**Audit plan:** Run dual-scoring 4 weeks to compare old NRFI POTD hit rate vs new ML/total POTD hit rate. Watch for:
- Are we LOSING good plays by demoting NRFI? (false-positive demotion)
- Do new ML/total headlines actually have edge? (POTD hit rate ≥ 55% target)
- Does the companion-signal gate isolate the cohort with real edge?
- Are LEAN supplementary tags read as "transparency" or "another play" by users? (UX question)

**Related:** [[sweat-dimensional-redesign]] (the sub-score split this rides on), [[may17-nrfi-coors-audit]] (5/17 audit rejecting NRFI 95+ stratification), [[v4-over-drift]] (why total_delta needs the conflict gate), [[may29-docket]] (queue context).
