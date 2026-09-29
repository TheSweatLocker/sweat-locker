---
name: postponement-api-truth-607
description: "6/7 permanent fix (commit 31cf77b): _is_postponed() in resolve_game_results now asks the MLB API for ground truth instead of inferring postponement from missing local rows. Closes the chain reaction that produced 6/6's silent recap blackout."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

The 6/6 silent recap blackout was a **two-stage chain reaction** that needed fixes at both layers:

**Stage 1 — data-layer silent fail** (fixed earlier 6/7, commit `69a287b`):
- `log_game_result()` POSTed `primary_play_computed_at` to `mlb_game_results`, but that column was only added to `mlb_game_context` (migration 20260604). Every write 400'd with PGRST204; the static strip-retry list didn't include it.
- Fix: replaced the hardcoded strip list with a dynamic loop that parses the missing column name from PGRST204 and drops + retries up to 8x.

**Stage 2 — resolver-layer false postponement** (fixed 6/7, commit `31cf77b`):
- With `mlb_game_results` empty, the resolver's `_is_postponed()` saw missing scores + 1-day-old game and inferred "Postponed". It graded SF Giants POTD as Push and CHW DotD as Push when both games had actually played (CHW won 6-3, Giants lost 3-2 in extras).
- Fix: `_is_postponed()` now hits `statsapi.mlb.com/api/v1/schedule` for ground-truth `detailedState='Postponed'` / `statusCode='DR'`. Returns True only on positive API confirmation. When API returns Final scores but our row is missing, `_backfill_score_to_results()` self-heals inline.
- Legacy fallback (no team names): threshold raised 1d → 3d, so a sub-day data lag never auto-Pushes.

**Defensive pattern this codifies (recurring class of bug):** Never let "our data is missing" silently equate to "the event didn't happen." Anytime the pipeline infers a *negative state* (postponed, didn't pitch, didn't play) from absence-of-data, it must verify against the upstream source of truth (MLB API, sportsbook line poller, etc.) before writing the negative state. The bug fires when our data path itself fails, AND the inference is silent.

**Why this matters for launch:** the user explicitly called the recap blackout "unacceptable" — paid-tier users will see receipts every day and any false-grade undermines the whole product. Verified the chain reaction can't repeat: 8/8 unit tests pass including a regression test on the actual 6/6 CHW@PHI misgrade.

**How to apply:** When auditing other resolver-class code, check for the same anti-pattern — `if not local_data: assume_negative_outcome`. Refactor to `if not local_data: verify_via_upstream_source_of_truth`. Likely other sites: prop resolver fallbacks, NRFI/YRFI grading when nrfi_result missing, sweat-card top_8 grading when sub-pick lookups fail.

Linked: [[project_v4_blackout_606]] (the 6/6 audit that surfaced the family of silent-fail bugs), [[feedback_validate_data_reaches_new_code]] (canonical pattern this is an instance of), [[project_607_top_item_dynamic_cohorts]] (the still-queued #1 item from last night).
