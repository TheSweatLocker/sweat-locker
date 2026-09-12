# SPEC — NFL + NCAAF Grading & Receipts Extension

**Status**: DRAFT · 2026-09-12 · Andy asked for full spec after Sunday NFL launch.

## Goal

Every shipped NFL + NCAAF pick reaches the app's Receipts surface with a Win/Loss/Push status, tied to what USERS SAW at pick time (not to the latest DB state). Ledger integrity for the record.

## Principles (non-negotiable)

1. **Grade what shipped.** Grader reads from the LOCKED snapshot (`jerry_cache.sharp_card_{date}.data.items` after 11am ET hard-lock), not from live `jerry_reads` which can be edited by pipeline downstream. Same for POTD, Ladder.
2. **Passes don't count against W-L.** A `call_market='pass'` is a disciplined skip. Track PASS count as a separate transparency stat (see Receipts UI note below), never as a Loss.
3. **Per-sport per-surface record.** Users need to see: NFL Sides 12-8-1, NFL Props 45-30, NFL Sharp Card 8-4, NFL POTD 2-1. Not a mashed "NFL 67-43" that hides which surface performed.
4. **Cross-sport aggregate for the app header.** "PRIME picks last 30d: 156-98" — sum across sports for the front-door metric, but per-sport for the breakdown drill-down.

## What we track — per sport

### NFL (all surfaces)

| Surface | Source table | Grader | Rollup |
|---|---|---|---|
| `nfl_sides` | `jerry_reads` (sport=NFL, call_market∈{ml,rl,spread,total}) | `grade_jerry_reads.py --sport NFL` | `agg_side_records('NFL')` |
| `nfl_props` | `prop_jerry_reads` (sport=NFL) | `grade_prop_jerry_reads.py --sport NFL` | `agg_prop_by_tier('NFL')` |
| `nfl_sharp_card` | `jerry_cache.sharp_card_{date}.data.items[sport=NFL]` | Snapshot-based via `agg_sharp_card` | Same script |
| `nfl_potd` | `jerry_cache.best_bet_{date}` when sport=NFL | `grade_potd.py` | `agg_potd` |
| `nfl_ladder` | `ladder_rung` (sport=NFL) | `resolve_ladder_results.py` | `agg_ladder` |

### NCAAF (sides + POTD/Sharp/Ladder only — NO props)

| Surface | Source | Grader | Rollup |
|---|---|---|---|
| `ncaaf_sides` | `jerry_reads` (sport=NCAAF) | `grade_jerry_reads.py --sport NCAAF` | `agg_side_records('NCAAF')` |
| `ncaaf_sharp_card` | Same as NFL | Same | Same |
| `ncaaf_potd` | Same | Same | Same |
| `ncaaf_ladder` | Same | Same | Same |
| **NCAAF props** | **N/A — policy: no college props** ([feedback_college_sports_no_props](../.claude/projects/c--Users-gomez-SweatShop/memory/feedback_college_sports_no_props.md)) | — | — |

## Cron sequence (order matters)

### Sunday night (NFL post-slate — 2am ET Monday)
```
1. resolve_nfl_results.py                      # nflverse CSV → nfl_game_results
2. resolve_nfl_props.py                        # ESPN box scores → prop grades
3. grade_jerry_reads.py --sport NFL --date <ET-yesterday>
4. grade_prop_jerry_reads.py --sport NFL --date <ET-yesterday>
5. aggregate_daily_records.py --date <ET-yesterday>
     # emits rows for: nfl_sides, nfl_props, nfl_sharp_card, nfl_potd, nfl_ladder
6. compute_surface_records.py --sport NFL      # 7d/30d/lifetime windows
```

### Sunday morning-after (already-graded Thu game + weekend NCAAF)
Same order, add `--sport NCAAF` calls. NCAAF resolver runs Sunday morning (Sat games done).

### Tuesday morning (MNF cleanup)
```
1. resolve_nfl_results.py                      # MNF final now in nflverse
2. grade_jerry_reads.py --sport NFL --date <Mon>
3. aggregate_daily_records.py --date <Mon>     # rewrites yesterday's aggregate to include MNF
```

## Locked-snapshot grader (CRITICAL — verify before shipping)

`agg_sharp_card(date)` MUST read `jerry_cache.sharp_card_{date}.data.items` as its source of pick truth. Each item has:
- `game_id, matchup, pick, tier, sport, type`

Grader looks up game_id in `<sport>_game_results` for outcome, resolves W/L/P per `type` (ml/spread/total/prop). Writes to `daily_surface_records` with `surface='sharp_card'` and per-sport variants.

**DO NOT** re-derive Sharp Card picks from live `jerry_reads` at grade time. Live data may have been edited by pipeline post-lock (e.g., `jerry_pick_scrub` runs). Only the locked snapshot represents what users actually saw.

Verification test:
```python
# Before running Monday's aggregate:
snapshot = jerry_cache.sharp_card_{Sun}.data.items
live_jr  = jerry_reads sport=NFL game_date=Sun with call_market in shipped set
# Diff — any snapshot item where live_jr call_side has flipped mid-day?
# If yes, LOG the diff, grade off snapshot, don't touch live.
```

Same principle for `grade_potd.py` — read `jerry_cache.best_bet_{date}` payload, not live pick.

## Missing pieces to build (backlog)

1. **NFL cron workflow file** — `.github/workflows/nfl_pipeline.yml` doesn't exist yet OR isn't running the resolve→grade→aggregate sequence in the right order. Verify + fix.
2. **`daily_surface_records` for `nfl_props`** — verify `agg_prop_by_tier` emits NFL rows (MLB-first pattern often skips other sports). If not, extend.
3. **Client-side Receipts render for NFL/NCAAF** — verify app has `SURFACES = ['sides', 'props', 'sharp_card', 'potd', 'ladder']` per sport and doesn't hard-render only MLB.
4. **PASS-count transparency stat** — extend `daily_surface_records.detail` to include `{shipped: N, passed: M}` so app can show "NFL Week 2: 15 shipped calls, 12 passed, 7-5 on shipped" — the honest split.
5. **Sharp Card snapshot-vs-live diff logger** — new script `audit_locked_pick_drift.py` compares locked snapshot to live jerry_reads, logs any drift for morning review. Doesn't block grading; just an audit trail.
6. **NCAAF grader Sunday-morning add** — `resolve_ncaaf_results.py` verified working per [project_ncaaf_grading_gap_908](../.claude/projects/c--Users-gomez-SweatShop/memory/project_ncaaf_grading_gap_908.md), but confirm it's in the actual daily cron.

## Verification checklist (Monday morning after first NFL Sunday)

- [ ] `nfl_game_results` has scores for every Sunday game
- [ ] `jerry_reads` sport=NFL Sunday rows have `result` populated
- [ ] `prop_jerry_reads` sport=NFL Sunday rows have `result` populated
- [ ] `daily_surface_records` has ≥5 NFL rows: `nfl_sides`, `nfl_props`, `nfl_sharp_card`, `nfl_potd`, `nfl_ladder`
- [ ] Sharp Card grade matches locked snapshot (spot-check 3 games manually vs live `jerry_reads` for drift)
- [ ] App Receipts tab shows NFL breakdown, not just MLB

## Backlog item priority for v1.0.1 batch

**HIGH**: #1 (cron workflow) + #2 (props rollup) — blocks Monday morning receipt render
**MED**: #4 (PASS transparency) + #5 (drift audit) — legitimacy features
**LOW**: #3 (client render) + #6 (NCAAF confirm) — already mostly there

## Related memory

- [feedback_sharp_card_composite_record](../.claude/projects/c--Users-gomez-SweatShop/memory/feedback_sharp_card_composite_record.md) — Sharp Card = surface_records.sharp + .prop combined
- [feedback_college_sports_no_props](../.claude/projects/c--Users-gomez-SweatShop/memory/feedback_college_sports_no_props.md) — NCAAF/NCAAB no props anywhere
- [project_ncaaf_grading_gap_908](../.claude/projects/c--Users-gomez-SweatShop/memory/project_ncaaf_grading_gap_908.md) — NCAAF grading UPSERT fix landed
- [project_cross_sport_grading_audit_908](../.claude/projects/c--Users-gomez-SweatShop/memory/project_cross_sport_grading_audit_908.md) — NHL missing resolver+grader; NBA resolver; NCAAB verify
