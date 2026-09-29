---
name: project-playbook-shadow-tracking-820
description: "Playbook PRIMEs that WOULD be juice-trap caught if the gate were on. Multi-day tracking to find the true \"confident juiced play\" line."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-19T22:31:35.323Z
---

**Purpose:** Find the LINE between "juice trap" (fade) and "confident play with juice worth eating." Playbook currently ignores juice; legacy demotes at -200+ via smart-refit rule. Tracking a specific cohort tells us if playbook's tier lift IS meaningfully sharp on juiced picks — or if legacy's discipline is right.

**8/20 locked JUICE-TRAP-VIOLATING cohort (playbook PRIME, would be legacy-demoted):**
- Pete Alonso hits_over @-260 · playbook PRIME 0.53 · refit 58.6
- Alec Bohm hits_over @-250 · playbook PRIME 0.647 · refit 62.1
- Junior Caminero hits_over @-260 · playbook PRIME 0.461 · refit 48.7
- Brandon Lowe hits_over @-225 · playbook PRIME 0.42 · refit 59.9
- Nico Hoerner hits_over @-220 · playbook PRIME 0.512 · refit 55.2
- Max Muncy hits_over @-220 · playbook PRIME 0.455 · refit 11.2
- Zack Gelof hits_over @-210 · playbook PRIME 0.438 · refit 55.5

**8/20 control cohort (playbook PRIME, spared or clean juice — for baseline):**
- Yandy Díaz hits_over (no odds yet, but likely ≤-180) · playbook 0.674
- Chandler Simpson hits_over (no odds) · playbook 0.674
- Luke Keaschall hits_over @-180 · playbook 0.652
- Tommy White hits_over @-210 (SPARED by smart rule — refit 66.8 clears floor) · playbook 0.472

**AM playbook-only cohort (unrelated to juice line, sanity baseline):**
- Robert Stock outs_under · playbook 0.48
- Jackson Jobe outs_under · playbook 0.452
- Taj Bradley outs_under · playbook 0.427
- Shohei Ohtani hits_over · playbook 0.412
- Walbert Ureña ha_under @+106 · playbook 0.41

**How to apply:**
- After 3-5 days, grade the JUICE-TRAP-VIOLATING cohort specifically.
- If they hit ≥ 70% (roughly the break-even at -230), playbook's signal breadth CLEARS the juice cost → keep no juice-trap gate for playbook
- If they hit 60-69%, mixed — refine the juice-trap sliding rule with playbook_score as a factor
- If they hit ≤ 60%, playbook needs the juice-trap gate before `PROP_PLAYBOOK_ENABLED` cutover
- Compare to control cohort (spared/clean juice) as baseline for playbook accuracy at this signal density

**Related:** [[feedback_batter_hits_juice_trap_803]] (the legacy discipline), [[project_playbook_signal_gap_819]] (the port that produced these lifts), [[project_playbook_shadow_tracking_820]].
