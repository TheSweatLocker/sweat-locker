---
name: project_discussion_queue_921
description: "Three items Andy queued 2026-09-21 for DISCUSSION (not silent implementation) — Split tab vision/value, NCAAB model+process readiness, NBA props evaluation. Then a v1.0.2 backlog reconciliation."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T23:02:18.168Z
---

Queued by Andy 2026-09-21 while working the money-flow stack. These are
**discussion items** — he wants a view and a recommendation, not code
landed unasked.

## 1. The Split tab — vision and value

"Discussion of vision and value of the Split tab, should we change it?
Idk what to?"

Steam Room tabs are Split / Ladder / Sharp / Ledger
([[feedback_steam_room_tab_names]]). Andy is unsure the Split tab earns
its place and has no replacement in mind — so the job is to work out what
it is FOR, whether that job is being done, and what else could occupy that
slot.

Worth bringing to that conversation:
  * What the tab actually shows today vs what a casual bettor takes from
    it ([[project_casual_bettor_ux_docket]] — translation not
    simplification).
  * The money-flow audit ([[project_fade_gate_performance_921]]): the
    SHARP_MOVE family shows no edge, while CONSENSUS-on-ML (68.4%) and
    fading NEUTRAL (62.1%) do. If the tab surfaces sharp-split language,
    it may be surfacing the part that does not work.
  * Source reality: OddsCrowd is dead, so any "3 sources agree" framing
    on that tab is now stale ([[project_money_flow_sources_921]]).

## 2. NCAAB — models, process evaluation, readiness

"Queue up ncaab models, process evaluation, where are we are we ready."

Season starts **2026-11-03** (sport_registry), state = preseason. So
~6 weeks out as of 09-21.

Context to pull in: [[project_nba_ncaab_discussion_queue_911]] (NBA
modelling + NCAAB pre-launch), [[project_all_sports_readiness_820]] (only
5/7 sports on ensemble_v2), [[feedback_college_sports_no_props]] (NCAAB
gets NO props anywhere), [[project_roster_physicality_823]] (NCAAB height
as a signal).

The question is genuinely "are we ready", so it needs an audit not an
opinion: ensemble coverage, resolver/grader, context population,
externals, write locks, elo/LR.

## 3. NBA props — evaluation

"Queue up NBA props evaluation."

NBA season starts **2026-10-21**, state = preseason, 14 context rows
(earliest 10-20). NBA `mc_probabilities` is empty (no nba_mc_simulator)
so the MC-dissent gate cannot fire.

Evaluate whether NBA props are worth shipping at launch at all, and if so
which families — MLB prop history says family-level discipline matters
enormously ([[project_hits_ban_verdict_917]],
[[feedback_prop_family_ban_three_layer]]).

## 4. THEN: v1.0.2 backlog reconciliation

"After all these are at the very least discussed lets talk overall backlog
1.0.2 big items: see if we are tracking the same things."

Andy wants to compare his mental list against mine. Do this AFTER the
three above. Starting points: [[project_v1_0_1_client_priorities]],
[[project_nfl_sota_rebuild_917]], [[project_pipeline_overhaul_909]],
[[project_public_receipts_integrity_918]], [[project_sweat_pick_badge_912]]
(Nov 3 launch — same date as NCAAB).
