---
name: dynamic-cohort-framework-607
description: "6/7 commit 6df5690: Dynamic cohort %s framework live. cohort_stats.py + cohort_lookup.py + jerry_cache row 'cohort_stats'. Phase 1 migrated 3 most-visible offenders; ~12 sites queued for Phase 2."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

User flagged 6/6 evening that hardcoded cohort %s in user-facing copy are a misrepresentation — backtest snapshots that drift the moment a new game is graded. Phase 1 framework now live.

**The actual drift on shipped numbers:**
- conf4_dog_rl: 82.6% (n=23 lifetime, 6/5 backtest) → **68.8% (n=16) live**
- conf4_fav_ml: 69.2% (n=13) → **64.3% (n=14) live**
- conf5_dog_rl: 50.0% (n=16) → **40.0% (n=15) live**
- conf6_dog_rl: 28.6% (n=7) → **16.7% (n=6) live**

Confirms the user's instinct — calling a 68.8% cohort "82.6%" daily is real misrepresentation.

**Architecture:**
- `cohort_stats.py` — recompute script that pulls from `mlb_game_results`, computes 20 cohorts (confluence ±2..±6 × dog/fav × RL/ML), upserts to a `jerry_cache` row keyed `'cohort_stats'`
- `cohort_lookup.py` — reader helper with 3 graceful-degrade failure modes (missing row / >36h stale / n<min_n) that all return None or fallback. In-process cached.
- Cron: recompute step in `.github/workflows/mlb_pipeline.yml` after the post-POTD card walk, `continue-on-error: true`
- Storage choice: `jerry_cache` row (not local JSON) so next morning's fresh runner reads it without filesystem persistence

**Phase 1 sites migrated (3):**
- `play_of_day.py:594` — 82.6% DOG RL → live
- `play_of_day.py:619-620` — 69.2% FAV ML → live
- `generate_dawg_of_day.py:570` — 82.6% DOG RL → live

**Phase 2 backlog (~12 sites, queued for app review):**
- `play_of_day.py:730, 778, 785, 979, 1129, 1560, 2308` — assorted lifetime/audit % strings
- `generate_dawg_of_day.py:499` — "58% cohort" (dog wRC+ edge)
- `generate_daily_degen.py:365, 373, 382, 385` — NRFI v2 model labels (these are model-output bands, not backtest cohorts — different recompute path needed)
- `generate_mlb_game_reads.py:384, 386` — NRFI tier band labels ("~69% audited 30d", "~58% audited")
- `scout_report.py:181, 191` — ump/audit %s
- `apply_prop_signal_override.py:173` — "64.7% UNDER hit rate"
- `generate_tonight_card.py:255` — "1.0-1.5 audit dead-zone (46% lifetime)"

**Migration pattern for Phase 2 callers:**
```python
from cohort_lookup import format_label
label = format_label('cohort_name', fallback='lifetime cohort')
# inline: f"PEAK confluence ({label} DOG RL)"
```

**Caller contract:** MUST handle None and surface a generic label instead of a number. Never ship a stale percentage.

Linked: [[project_607_top_item_dynamic_cohorts]] (this closes the #1 item user called last night), [[project_postponement_api_truth_607]] (parallel canonical-source-of-truth fix shipped today).
