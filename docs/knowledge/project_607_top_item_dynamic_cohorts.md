---
name: 607-top-item-dynamic-cohorts
description: "6/7 #1 priority: every hardcoded cohort win-rate label is a misrepresentation — they change with each graded game. Build nightly recompute + label-from-stored-value. User called this out tonight, wants big discussion + full app review tomorrow"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

User flagged 2026-06-06 evening: any hardcoded cohort percentage in user-facing copy is a misrepresentation. Cohort hit rates shift with each graded game; pinning to a one-time backtest number means the surface is lying every day after it was computed. Specifically called out: the "82.6% DOG RL lifetime cohort" string in `generate_dawg_of_day.py:570` + `play_of_day.py:594`. Inline `n=23` ships in commit 94d444a as a stopgap but doesn't fix the underlying drift.

**Why:** User trust + paid-launch readiness. App review is tomorrow — surfaces that look like real-time stats but aren't will be picked apart. "Don't show me a number unless it's actually current" is the rule.

**How to apply tomorrow morning (FIRST item — user is calling it):**
1. Audit every hardcoded cohort percentage in user-facing copy across the repo. Initial hit list:
   - `generate_dawg_of_day.py:570` — 82.6% DOG RL
   - `play_of_day.py:594` — 82.6% DOG RL
   - `play_of_day.py:619-620` — 69.2% FAV ML PEAK confluence
   - `_backtest_side_rl.py:6, 36` — internal, not user-facing
   - Grep for `%` in any string that renders to the app (sweat dim drivers, dawg signals, jerry reads).
2. Build `mlb_pipeline/cohort_stats.py` + `cohort_stats` table (or JSON file in `models/`): each named cohort gets `{name, win_pct, n, computed_at}`. Nightly cron recomputes from `mlb_game_results` + `mlb_game_context`.
3. Refactor labels to read from the lookup: `cohort_stats.get('conf4_dog_rl').format()` → "PEAK confluence (+4 — {pct}% DOG RL, n={n}, refreshed {computed_at})".
4. Add a freshness gate: if `computed_at` > 36h old, surface a "⚠ stale" suffix or fall back to "[refreshing]" rather than show drifted numbers.
5. Same pattern for all the prop-cohort percentages threaded through sweat dim driver strings and Jerry reads.

**Broader app-review docket tomorrow (user said "big discussion + full app review"):**
- Hardcoded cohort numbers (above) — #1
- Where else are we showing point-in-time values as if they're live? (e.g. "30d audit ~58%" labels in NRFI tier strings, Phase 2 recal cohort references, etc.)
- The architectural gap surfaced today: Padres game qualified for PEAK cohort (82.6% n=23 lifetime, conf=+4 DOG RL) but DAWG selection went to CHW (more total signals at conf=+5 over-saturated). Should the PEAK band override raw total-conviction sums for the DAWG slot, or stay separate?
- Strategic launch timing — readiness for paid tier by next baseball season.

Linked: [[project_jerry_attribution_validator]] (commit 94d444a that prompted this question), [[project_june5_cohort_audit]] (the n=640 scan that produced the frozen 82.6%/69.2% numbers), [[project_post_launch_roadmap_may_to_nfl]] (the v1.0 → v2.0 backside cadence this work feeds into).
