---
name: sweat-rescore-timing-gap-605
description: 6/5 architectural fix — play_of_day.run() now always writes sweat even when POTD is locked; refresh_imminent_games.py re-runs play_of_day after prop regen
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Shipped 6/5 (commit 0e42648).

**Problem:** `play_of_day.run()` early-returned at two points (manualOverride lock, 11am-2pm window). That short-circuited the per-game sweat-score writes that happen LATER in the function. Any caller during a lock window silently no-op'd on sweat. The watchdog `refresh_imminent_games.py` regenerated props every 30 min on lineup confirmation but never re-ran play_of_day, so sweat froze at the 6am/2pm cron values while props drifted forward.

**6/5 example:** Manual trigger at 1:21pm ET wrote sweat scores with morning props. Lineup-confirmation watchdog at 1:26pm regenerated props (PIT@ATL went 2 PRIME no-book → 3 PRIME aligned OVER; SF@CHC went 3 → 5 PRIME hits Over). Sweat stayed frozen. Live rescore would have produced PIT@ATL 82/PRIME vs DB-stored 52. Net drift across slate: +30/+15/+12/+10/+9 on six games.

**Fix has two parts:**

1. [play_of_day.py:1812-1851](mlb_pipeline/play_of_day.py#L1812-L1851) — refactored lock check into `skip_potd_selection` flag at entry; per-game scoring loop always runs; POTD selection gated at end (line 2024). 2pm+ override threshold (+20) unchanged.

2. [refresh_imminent_games.py:467-478](mlb_pipeline/refresh_imminent_games.py#L467-L478) — added `play_of_day.run()` call after the existing `generate_props.run()` block. Order: props → sweat → Daily Degen → Sweat Card.

**Why:** User asked for manual triggers anytime without messing things up. POTD lock is now load-bearing for ONE concern (don't clobber posted POTD) but no longer leaks into "don't refresh sweat." Two responsibilities cleanly split.

**How to apply:** When debugging stale sweat scores, FIRST check if the timestamp gap between `primary_play_computed_at` and `sweat_tier_locked_at` is large — that's the rescore gap. Now the watchdog closes it within 30 min of lineup confirmation. Smoke-tested 6/5 ET 15:00: PIT@ATL 52→68 PRIME, TB@MIA 55→67 STRONG, POTD NYM 80 unchanged.

Linked: [[pm-cron-live-game-prop-overwrite]] (related — 2pm overwrite on live games), [[june5-side-dim-rework]] (same-day side reweight).
