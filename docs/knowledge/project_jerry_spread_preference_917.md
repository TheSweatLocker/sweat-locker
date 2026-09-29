---
name: project-jerry-spread-preference-917
description: Queued — Jerry should prefer spreads over -200+ ML picks unless PRIME tier. -200 ML juice is a documented trap; spread often gives same-team exposure at better price
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-17T23:24:52.388Z
---

**Andy 9/17 late-eve directive:**
"Also want to work on Jerry picking a spread eventually unless Prime of Jerry picking -200 MLs is winining"

Translation: when Jerry lands on a heavy-fav ML pick (worse than -200 juice), route it to the spread on the same team unless the pick is PRIME tier. -200+ ML is [[project_juice_fav_rl_trap_724]] (29% cover on -1.5 for heavy favs) + [[feedback_heavy_fav_ml_trap_803]] (swap correlated) territory.

**Current state (2026-09-17):** NFL LR override in [defensive_gates.py] already does this for MLs beyond -300 juice — reroutes to spread with `audit_note: "LR override · NFL LR wanted HOME but ML odds too juicy (>-300) — rerouted to spread"`. Verified on NO@BAL: LR wanted BAL ML, but market was -380 → rerouted to BAL -7.5.

**What's queued:**
- Extend LR-override-style reroute to Jerry composer (`generate_nfl_game_reads`)
- Threshold: worse than -200 (not just -300)
- Exception: PRIME tier — allow heavy-fav ML if the tier is PRIME (high-conviction favs pay their juice)
- Backtest first: does 30d of Jerry heavy-fav ML picks lose money vs the same-team spread? If yes, ship the reroute. If no, leave alone.

**Not shipping tonight** — Andy said "let's just work on the list" (NFL hardening + v1.0.2 punchlist). Queued for later.

Related: [[feedback_potd_juice_gate_803]] (POTD max -200/-250 juice), [[feedback_heavy_fav_ml_trap_803]], [[project_juice_fav_rl_trap_724]].
