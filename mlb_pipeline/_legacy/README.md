# `_legacy/` — deprecated files preserved for reference

Files moved here have **zero runtime callers** in any workflow or script AND
their functionality is superseded by another script. Preserved (not deleted)
so their code + git history remain available if needed.

**To restore any file:** `git mv mlb_pipeline/_legacy/<file> mlb_pipeline/<file>`

**Moved 2026-08-22 during pipeline audit cleanup:**

| File | Superseded by | Verification |
|---|---|---|
| `umpires.py` | `umpires_scrape.py` | Static seed of 30 umpires (6 unique: all retired — Angel Hernandez, Joe West, Jim Joyce, Gerry Davis, John Hirschbeck, Tim Welke). `umpires_scrape.py` has 75 current active umpires. Zero callers. |
| `ufc_scraper.py` | `ufc_card_scraper_v3.py` | v0 fighter-stats scraper (broken since UFCStats bot check May 2026). Only refs are docstring mentions in `ufc_card_scraper.py` (also legacy) and `ufc_espn_enrich.py`. |
| `ufc_card_scraper.py` | `ufc_card_scraper_v3.py` | v1 UFCStats scraper (proof-of-work JS challenge broke it May 2026). Only refs are docstrings in v2/v3 and a stale reference in `ufc_score_card.py` docstring. |
| `ufc_card_scraper_v2.py` | `ufc_card_scraper_v3.py` | v2 ESPN-backed. v3 is the current wired-in caller. Zero callers. |
| `resolve_external_picks.py` | `resolve_externals.py` | Older name for the same grader. All workflows call `resolve_externals.py`. Zero callers. |
| `grade_ledger_suggestions.py` | `grade_ledger_snapshots.py` | Snapshots version uses locked odds (correct). Suggestions version predates the snapshot lock. Zero callers. |
| `compute_cohorts_v2.py` | `ensemble_scorer.py` | v2 cohort shadow computer. Superseded by ensemble_v2 which subsumes the cohort logic via signal_sources. Zero callers. |

**NOT moved (still worth keeping in main pipeline):**
- `read_live_tier_record.py` — module imported by `generate_sweat_card.py:1705`
- `track_live_tier_record.py` — writes to `jerry_cache.live_tier_records` which read_live still reads. Chain safer left intact until audit_tier_calibration replaces the read path too.
- `pull_ncaab_teamrankings_trends.py` + `enrich_ncaab_team_trends.py` — NCAAB legacy pair, only docstring refs to each other, but keeping together for a focused NCAAB cleanup session
- `fetch_nfl_consensus.py` — imported by `generate_nfl_props.py` (itself orphan). NFL cleanup will handle both together.
- `nba_pipeline.py`, other pre-launch NFL/NCAAB scaffolding — flagged in audit but tied to unreleased sport work.

If you need a file restored, `git mv` it back and it's live again.
