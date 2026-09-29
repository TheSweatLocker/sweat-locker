---
name: project-nhl-resolver-build-908
description: "🎯 9/8 NHL resolver + grader wiring queued for build. Season starts Oct 8 (30 days out). Both resolve_nhl_results.py and grade_jerry_reads NHL entry are missing today."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T14:51:52.564Z
---

**Queued 9/8 late afternoon.** Cross-sport grading audit (project_cross_sport_grading_audit_908) found NHL missing BOTH resolver AND grade_jerry_reads entry. Season opens Oct 8 — must ship before then.

## Build spec

### 1. `mlb_pipeline/resolve_nhl_results.py`

Mirror the pattern from `resolve_ncaaf_results.py`:

**Data source:** NHL API (free) — per `project_nhl_scope` memory, we're using NHL API + MoneyPuck. Endpoint pattern:
```
https://api-web.nhle.com/v1/schedule/YYYY-MM-DD
https://api-web.nhle.com/v1/gamecenter/{gameId}/boxscore
```

**Flow:**
1. Pull NHL API schedule for target date range (last 7 days + today)
2. Extract final scores (only completed games — status = 'FINAL' or 'OFF')
3. Match to our internal game_id format (need to confirm format used by existing NHL ingest)
4. PATCH `nhl_game_results` with home_score, away_score, computed spread/total outcome
5. UPSERT any missing rows (per the pattern from resolve_ncaaf_results 3-pass fix — reverse-sweep from jerry_reads is critical)
6. Kick `resolve_externals.py --sport NHL` at end

**Confirmed schema on `nhl_game_results` (verified 9/8 late-night query):**
```
id, game_id, game_date, home_team, away_team,
home_score, away_score, total_goals, home_win,
close_home_ml, close_away_ml, close_puckline, close_total,
spread_result, total_result,
went_to_ot, went_to_so,
resolved_at
```

Table EXISTS — no migration needed. Every field needed by grader is
already there. Column names to note when writing resolver:
- `close_puckline` (not close_spread) — grader dispatch map needs
  `'NHL': 'close_puckline'` if it currently expects close_spread
- `went_to_ot` + `went_to_so` — two separate flags, not a combined
  'overtime' bool. Resolver should set both correctly (OT that
  ended in regulation-plus wins under NHL rules → went_to_ot=true;
  shootout → went_to_so=true which implies went_to_ot=true too)
- `home_win` — bool, use for ML grading
- `total_goals` — computed = home_score + away_score (populated by
  resolver; downstream uses this vs close_total for O/U grading)

### 2. Add NHL to `grade_jerry_reads.py`

Line 30 area — add:
```python
'NHL': 'nhl_game_results',   # sport → results-table map
```

Line 47 area — add:
```python
'NHL': 'spread_result',      # spread result column varies per sport
```

Verify column names match what the resolver writes.

### 3. Wire into workflow

Add to `.github/workflows/mlb_grade_overnight.yml` after the NCAAF resolver step:
```yaml
- name: Resolve NHL game results
  continue-on-error: true
  env:
    SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
    SUPABASE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY || secrets.SUPABASE_KEY }}
    PYTHONIOENCODING: utf-8
  run: |
    cd mlb_pipeline
    python resolve_nhl_results.py --skip-external || echo "NHL resolver non-fatal (offseason ok)"
```

Also add to `nhl_pipeline.yml` if that workflow exists (per project_nhl_rebuild_status_817 it should).

### 4. Verification

Once season starts (Oct 8):
1. Run `python resolve_nhl_results.py --dry-run` — should find current-week games
2. Run `python grade_jerry_reads.py --sport NHL` after resolver — should grade jerry_reads for played games
3. Verify morning_brief.py shows NHL row in Section 1 grading completeness
4. Verify `daily_surface_records` includes NHL rows

## Timeline

- **9/22-9/27:** build resolver + wire grader (2 weeks before season)
- **10/1-10/7:** dry-run test against preseason data if available
- **10/8+:** live monitoring via morning_brief.py

## Reference

- `resolve_ncaaf_results.py` — closest template (also uses free external API for scores + UPSERT pattern shipped 9/8 in 41f656ae)
- `grade_ufc_jerry_reads.py` — alternate pattern if we want a dedicated NHL grader (probably overkill)
- `project_nhl_rebuild_status_817` — NHL scoreboard rebuild 8/17, 1,335 games backfilled
- `project_nhl_scope` — NHL data sources decision (free NHL API + MoneyPuck)
- `project_cross_sport_grading_audit_908` — sibling audits for NBA (Oct 24) + NCAAB (Nov 3 — already wired)
