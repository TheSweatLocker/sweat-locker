---
name: close-spread-sign-bug-914
description: "🚨 close_spread sign convention differs across sports: NFL positive=home fav; MLB/NCAAF negative=home fav. Fixed 3 hot-path graders; discover_patterns queued for dedicated refactor with per-field sign registry."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-14T12:18:27.413Z
---

**Discovered 2026-09-14** during morning audit — subagent flagged sign inversion in NCAAF scorecard, follow-up audit confirmed cross-sport inconsistency.

## The bug

`close_spread` column stores different sign conventions across sports:

| Sport | Convention | Example |
|-------|-----------|---------|
| **MLB** | NEGATIVE = HOME favored | cs = -1.5 means home lays 1.5 |
| **NFL** | **POSITIVE = HOME favored** | cs = +6.5 means home lays 6.5 |
| **NCAAF** | NEGATIVE = HOME favored | cs = -6.5 means home lays 6.5 |
| NBA/NHL/NCAAB | Assumed NEGATIVE (untested) | — |

Additionally, `sp_plus_pred_spread` in NCAAF ctx stores predicted home
margin (positive = home wins by X), which is OPPOSITE sign to
`close_spread` for NCAAF. So even within NCAAF, `sp_plus_pred_spread + close_spread` mixes conventions.

## Verification

Sampled 3 recent games per sport, checked which side has ML favorite (H<A means home fav) vs close_spread sign. All three sports' samples were consistent within-sport but inconsistent between:
- MLB: 3/3 samples had cs<0 with H<A (home ML fav) → NEG = home fav
- NFL: 3/3 samples had cs>0 with H<A (home ML fav) → POS = home fav
- NCAAF: 3/3 samples had cs<0 with H<A (home ML fav) → NEG = home fav

## Impact + fixes

**Fixed 2026-09-14 (this session):**
1. `signal_attribution_grade.py:_grade_pick` — now takes `sport` arg, normalizes `home_line = cs if sport=='NFL' else -cs` before cover math. Directly affects the LR_SHADOW / GOAT / ANCHOR / cohort record tooltips.
2. `enrich_team_form_universal.py:_grade_ats` — same normalization. Affects ATS venue-split records that feed team-form signals.
3. `similar_games.py:_outcome_summary` — same normalization. Affects `rl_home_covered_pct` in outcome summaries.

**Known-buggy, queued:**
4. `discover_patterns.py:_football_extractors` — universal `v + cs` deltas mix NFL/NCAAF conventions AND cross-mix `sp_plus_pred_spread` (home margin) with `close_spread` (home line). Needs a per-field sign registry, not a one-line fix. Documented in code with pointer to this memory.

## How to apply

**When writing new spread/cover math:** always take a `sport` parameter and normalize to a "home_line" scalar (positive iff home favored) using:

```python
home_line = close_spread if sport == 'NFL' else -close_spread
# now: home cover iff (margin - home_line) > 0
```

**When reading an existing formula and seeing `+ close_spread`:** verify the caller. If it works on MLB/NCAAF only, `+ cs` is correct (their convention). If NFL is in scope, need to flip.

**Root cause:** each sport's odds pull writer chose the convention that felt natural for that sport at the time, without a codebase-wide standard. Retrofitting a universal convention would require rewriting every odds writer + backfilling every historical row — expensive. The sport-aware helper pattern is the safer path.

## Related

- [[project_ncaaf_lr_total_dead_914]] — NCAAF LR total model output is dead (separate issue)
- Subagent report 2026-09-14 morning session (NCAAF scorecard investigator flagged the sign issue)
