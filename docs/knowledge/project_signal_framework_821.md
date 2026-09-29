---
name: project-signal-framework-821
description: 🎯 SWEAT LOCKER STANDARD 8/21 — every prop/game must evaluate comprehensive checklist. Missing factor = signal_sources gap.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-21T22:18:51.232Z
---

**8/21 user directive:** every prop, side, and total that the Sweat Locker system rates MUST evaluate a comprehensive checklist of factors. Missing a factor is a scoring bug — a `signal_sources` gap that needs to be filled, not an acceptable simplification.

**Universal philosophy:** anything that can affect the outcome should be evaluated. Specific factors differ by sport but the standard applies cross-sport (MLB, NFL, NBA, NHL, NCAAF, NCAAB, UFC).

**Full framework doc:** `mlb_pipeline/docs/SIGNAL_FRAMEWORK.md` (committed 8/21).

**MLB Pitcher Prop Checklist (25 factors):** pitcher form (L3, xERA, home/road split, 1st inn, rest), career + recent vs-team (BAA/ERA/K9/IP), opp lineup form/K%/BABIP/barrel%/ATS, own bullpen, park, weather, ump, platoon, market/split/movement, model projection sanity.

**MLB Batter Prop Checklist (14 factors):** batter L7/L14, vs pitcher career, vs LHP/RHP, home/road, lineup spot, team offense, opp starter/pen, park, weather.

**MLB Sides/Totals Checklist (16 factors):** both starters/pens/offenses, ATS L10, home/road, H2H, splits, line movement, weather, park, rest, sharp scenarios, models.

**When shipping a new signal:**
1. Which factor slot is it filling? (map to checklist)
2. Is there a MIRROR for opposite direction? (learn from platoon/RL/OC-handler bugs where home-only signals created systemic bias)
3. Is threshold tuned? (learn from Luzardo .268 vs .270 miss)
4. Verifier vs primary tiering respected?
5. Update SIGNAL_FRAMEWORK.md.

**Session context: 30+ signals shipped 8/21 to close checklist gaps.** Still open: batter platoon (pitcher_throws NULL), per-start hit history (aggregate only), umpire ctx per game.

**Enforcement:** coverage_audit.py flags any prop with <12/25 factors evaluated OR single class >45% of score contribution.

Related: [[project_playbook_signal_gap_819]], [[project_playbook_reweight_821]], [[feedback_verify_ml_direction]].
