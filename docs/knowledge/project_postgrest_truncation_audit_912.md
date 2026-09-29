---
name: project-postgrest-truncation-audit-912
description: "Systemic PostgREST 1000-row truncation bug found across 20+ pipeline scripts — some user-facing, some read-only; pending sweep prioritized by user impact"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-13T17:29:47.917Z
---

**Fact:** PostgREST silently caps `SELECT` responses at 1000 rows regardless of the client-passed `limit=`. Many pipeline scripts pass `limit=5000` / `limit=2000` and silently receive only 1000 rows without error or warning. This bug bit us on 9/12: [[project_v1_0_1_client_priorities]] item #16 was the visible tip, actual root cause was `ncaaf_game_context.load_team_games_played` silently receiving 1000 of 3829 rows for 2025 season → cascaded into Texas averaging 1086 pass yds/gm on the Ohio State card. Fixed in 39a12c28.

**Why:** Every SELECT that could return >1000 rows without Range-header pagination or filtering is quietly wrong. `limit=5000` is a false comfort — the value is truncated server-side. This is a landmine class of bug, not a one-off.

**How to apply:** When next asked for a pipeline audit, prioritize these known-affected files. Impact ranking:

**Tier A (user-visible surfaces) — ALL FIXED as of 2026-09-13:**
- `generate_sweat_card.py:937` — fixed aa2f968f
- `dedup_prop_dupes.py:125,206` — fixed ae3a46fe
- `grade_prop_playbook.py:110,222` — fixed ae3a46fe
- `generate_daily_degen.py:146` — fixed ae3a46fe
- `play_of_day.py:2890` — fixed 2c57126c
- `apply_prop_refit.py:274` — fixed 21e9a883 (was cause of "refit=0 hidden PRIMEs" for 5+ days)
- `sweep_prop_coverage.py:278` — fixed d5f2e192 (was cause of "pitcher PRIME wipe" on every sweep run)

**Tier B (analysis + audit scripts, read-only):**
- `audit_prop_playbook_shadow.py:60,77`
- `audit_projection_accuracy.py:61`
- `audit_data_accuracy.py:69,87,283,304`
- `audit_signal_attribution.py:66`
- `audit_tier_integrity.py:67`
- `discover_patterns.py:436,447`
- `compute_model_track_records.py:125`
- `compute_game_bucket_roi.py:173`
- `compute_prop_bucket_roi.py:235`

**Tier C (probably safe — narrow queries):**
- `apply_prop_refit.py:151` (per-prop_type)
- `nfl_prop_injury_filter.py:78,102` (single slate)
- `ufc_grader.py:223` (UFC has few events)
- `nfl_espn_projections_pull.py:118` (32 teams max)

**Canonical fix pattern:** Use Range-header pagination — see `ncaaf_game_context.load_team_games_played` (39a12c28) or `cleanup_stale_coverage_props._paged` for reference. `for page in range(N): headers={..., 'Range': f'{lo}-{lo+999}'}; if len(chunk) < 1000: break`.

Related: [[feedback_grading_zero_fail_912]] (pagination discipline for grading), [[feedback_methodical_no_rerun_spam]].
