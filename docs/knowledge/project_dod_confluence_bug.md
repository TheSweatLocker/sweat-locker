---
name: dod-confluence-direction-bug-fixed-5-16-followups-queued
description: DOD scorer was reading signal_confluence_net as supporting the dog when it actually pointed to model_pick. Fixed 5/16. Three follow-ups queued for 5/17 audit.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## What broke

`generate_dawg_of_day.py` was applying confluence bonuses based on `signal_confluence_net > 0` without checking which *side* the signals favored. The convention is `net = support - against` where `support` = signals matching `model_pick` — so a positive net could favor either home OR away depending on whose side the model picked. When the dog was on the *opposite* side of the model pick, the DOD still got a PRIME +12 conviction bonus and printed "PRIME confluence (+4 signals stack on [Dog])" in the narrative.

**Concrete instance (5/16):** ARI/COL — confluence net +4 with breakdown all 'away' (favoring ARI). DOD picked COL +120 home dog and bumped conviction +12 with the misleading narrative. COL was actually fighting +4 signals.

## What was fixed today

In `score_dawg()`, replaced raw `confluence_net` reading with breakdown parsing:
- Count `home_count` vs `away_count` in `signal_confluence_breakdown`
- Determine `conf_side` (home/away of majority)
- Compare to `dawg_side`
- Apply bonus only when aligned, penalty when opposed (PRIME -15, STRONG -8)

After fix: COL dropped from STRONG 67 → LEAN 40 with explicit "⚠ PRIME confluence AGAINST — fade risk" signal.

## What's still queued for 5/17

### 1. Hard-reject PRIME against
Today's slate after the fix had **only one DOD candidate** (COL) because BAL/WSH moved to pickem (-108/-108) and no other game had `dog_edge ≥ 1.3`. COL still surfaced as DOD despite the confluence warning because nothing else qualified. Should add: if conviction < 50 AND PRIME confluence against, **skip DOD entirely for the day** rather than publish a fade-risk pick.

### 2. Add "model-favored pickem" path
BAL/WSH was the analytically correct play today (spread delta +2.29, STRONG +2 confluence aligned, WSH 5.31 home R/G), but failed the `home_ml >= 100 / away_ml < 0` dog gate because lines moved to even juice. Need a parallel surface — call it "Best ML Edge" or "Model Pick" — that captures these cases without being mislabeled as a Dawg.

### 3. Backfill audit: did past DOD picks suffer from the bug?
Query `daily_dawg` joined to `mlb_game_results` for the last 30-90 days. Split into:
- **Aligned cohort:** DOD pick's confluence_side matched dawg_side
- **Misaligned cohort:** confluence_side opposed dawg_side (the bug's exposure)
- **Neutral cohort:** no confluence majority

Hypothesis: misaligned cohort underperformed both other cohorts. If true, the post-fix model is materially better than the pre-fix model and we should flag a "DOD restart" point in the calibration tracking (don't blend old DOD performance with new).

See [[project_may15_calibration_notes.md]] for related 5/17 audit items.

## Manual override for tonight

Wrote WSH directly to today's `daily_dawg` row via `_override_dod_wsh.py`. STRONG 75 conviction. Tomorrow's cron will run the bug-fixed scorer fresh — no manual intervention needed.

**Files touched 5/16:**
- `mlb_pipeline/generate_dawg_of_day.py` — score_dawg() confluence block (lines ~262-303)
- `mlb_pipeline/_override_dod_wsh.py` — one-off override script (keep for reference)
- `mlb_pipeline/_check_dod_candidates.py` — diagnostic helper (keep for audit)
