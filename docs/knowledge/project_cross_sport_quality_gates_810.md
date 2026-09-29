---
name: cross-sport-quality-gates-810
description: "2026-08-10 architecture: all Jerry quality-control lessons from MLB now apply to NFL/NCAAF/UFC/NHL/NCAAB/NBA via sport registries. Adding a sport = fill in the registry entries, don't rebuild the tools."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-11T01:38:48.094Z
---

**2026-08-10**: user directive after two BAL/BOS ML losses — "all Jerry / MLB lessons need to apply to other sports so we don't go through this again with every sport."

Response: refactored 3 core quality-control modules to sport-plugin architecture. New sports register in a dict; the tool logic stays sport-agnostic.

## The three cross-sport gates (all shipped)

### 1. `jerry_stat_verifier.py` — MLB API cross-check

**Was**: MLB-only. Regex extracts numeric claims from Jerry prose + cross-checks against MLB Stats API.

**Now**: `PROVIDERS` dict registry. `verify(prose, player, sport='NFL')` dispatches to per-sport implementation.

Registered stubs (return no-op ok=True until implemented):
- NFL → ESPN QB game log (TODO: implement)
- NCAAF → CFBD API QB stats
- UFC → ufc_fighter_stats table + fight_results
- NHL → NHL Stats API (free)
- NCAAB → KenPom + basketball-reference
- NBA → nba_api

**MLB implementation still live** (only sport where verifier actively catches hallucinations right now).

### 2. `substitute_generic_starter_refs()` in `validate_jerry_read.py` — Layer D scrub

Was: MLB-only "the opposing starter/pitcher" → real pitcher name.

Now: sport-dispatch wrapper:
- MLB → substitute_generic_pitcher_refs (original)
- NFL/NCAAF → _substitute_generic_qb_refs ("opposing QB/quarterback")
- UFC → _substitute_generic_fighter_refs ("opposing fighter")
- NHL → _substitute_generic_goalie_refs ("opposing goalie/goaltender")
- NCAAB/NBA → no-op (basketball has no single starter role)

Same proximity resolution (last-mentioned name → substitute the other).

### 3. `collapse_sharp_fade_violations.py` — sharp-fade discipline gate

**Was**: MLB-only, hardcoded to `mlb_game_context`.

**Now**: `SPORT_CONFIG` dict with per-sport ctx table + column names. Rule A (sharp $ ≥65% same side + 2+ models opposing) + Rule B (all 3 models consensus opposite Jerry) apply universally.

Registered: MLB, NFL, NCAAF, NCAAB, NHL, NBA. CLI: `--sport ALL` loops all.

## Which tools automatically inherit the sport-registry pattern

Already sport-agnostic (needed no changes):
- `jerry_pre_publish_audit.py` — accepts `--sport` arg, has MLB-specific gates but core structure works cross-sport
- `compute_scenario_audit.py` — sport-agnostic schema, per-sport `build_scenarios_X()` extractor (only MLB implemented so far)
- `write_line_snapshot.py` — `CTX_TABLE` dict per sport
- `grade_jerry_reads.py` — `RESULTS_TABLE` dispatch (MLB/NBA/NFL/NCAAF/NCAAB registered; UFC uses separate `grade_ufc_jerry_reads.py`)

MLB-only (need porting per-sport):
- `apply_prop_refit.py` + `_fit_prop_refit_weights_v2.py` — only reads `mlb_pipeline_props`. NFL prop refit blocked on graded data volume; will retrain in Nov after ~1000 NFL props resolve.
- `apply_refit_verdict_override.py` — same MLB-only dependency
- `collapse_pitcher_thesis_contradictions.py` — MLB pitcher-specific logic. NFL equivalent would be QB thesis (pass_yds + TDs + INTs coherence)
- `collapse_prop_jerry_contradictions.py` — reads any sport's prop_jerry_reads

## Pattern for adding a new sport

1. Add ctx table config to `SPORT_CONFIG` in `collapse_sharp_fade_violations.py`
2. Add stat verifier stub to `PROVIDERS` in `jerry_stat_verifier.py` (returns no-op initially; fill in when real API access secured)
3. Add "opposing starter" scrub function to `validate_jerry_read.py` (if sport has a single-starter role — pitcher/QB/goalie/fighter)
4. Add `build_scenarios_X()` extractor to `compute_scenario_audit.py`
5. Register results table in `grade_jerry_reads.RESULTS_TABLE`
6. Add sport to `sport_registry` DB table (already sport-universal per `project_sport_registry_backend_810`)

## What still needs per-sport implementation (open work)

Priority = sport launch order (NCAAF Aug 22, NFL Sept 4, NHL Oct 8, NBA Oct 21, NCAAB Nov 3):

- **NCAAF stat verifier** — CFBD QB game log integration (before Aug 22)
- **NFL stat verifier** — ESPN or nflverse QB integration (before Sept 4)
- **NFL prop refit weights** — need ~1000 graded props first; retrain scheduled for late Sept
- **NFL pitcher-thesis analog** — QB stat coherence validator (post-Sept 4)
- **NCAAB scenario extractor** — build_scenarios_ncaab() with KenPom-based dimensions (before Nov 3)
- **NHL scenario extractor** — build_scenarios_nhl() with goalie/xG dimensions (before Oct 8)
- **UFC stat verifier** — populate from ufc_fighter_stats (any time)

## Related memories

- [[project_refit_v2_expansion_810]] — refit is MLB-only; extension per-sport documented above
- [[project_sport_registry_backend_810]] — DB-side sport catalog
- [[feedback_never_generic_pitcher_ref_809]] — Layer D MLB origin story
- [[feedback_sharp_money_fade_808]] — sharp discipline origin
