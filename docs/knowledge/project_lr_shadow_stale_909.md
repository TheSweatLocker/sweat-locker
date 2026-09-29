---
name: project-lr-shadow-stale-909
description: "🚨 9/8: LR override runs on partial features → stores stale shadow. Same games get IDENTICAL 0.41/0.47 despite different close lines. Pipeline still winning (props +148u) but LR decisions are stale."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T19:34:39.174Z
---

**User surfaced 9/8 evening:** Sharp Card LR values look wrong — same value across many games.

## Diagnosis

**LR override runs ONCE per pipeline run** at the time primary_play is computed. If that computation happens BEFORE:
- Lineups land
- Close lines lock
- Weather/wind updates

...then most of the 107 LR features are missing → imputed to medians → identical LR output across games.

## Today's evidence (2026-09-08)

**Stored LR shadow (from AM pipeline):**
| Game | Close spread | Close total | Stored LR p_home | Stored decision |
|------|-------------|-------------|------------------|-----------------|
| TOR@ATH | +1.5 | 9.5 | **0.4076** | coin flip (demote) |
| CHC@MIL | -1.5 | 7.0 | **0.4076** | coin flip (demote) |
| TB@ATL | +1.5 | 8.5 | **0.4076** | coin flip |
| CLE@BAL | -1.5 | 8.0 | **0.4076** | coin flip |
| WSH@SD | -1.5 | 8.0 | **0.4076** | coin flip |
| STL@SF | +1.5 | 7.5 | **0.4076** | coin flip |
| TEX@SEA | -1.5 | 8.0 | **0.4076** | coin flip |
| CHC@MIL | -1.5 | 7.0 | **0.4076** | coin flip |

**Live LR (re-run just now with fresh ctx):**
| Game | LIVE LR p_home | LIVE decision |
|------|----------------|--------------|
| TOR@ATH | **0.20** | PRIME AWAY (Blue Jays) |
| CHC@MIL | 0.47 | coin (correct) |
| CLE@BAL | **0.42** | STRONG AWAY (Guardians) |

## Impact on the pipeline

- Picks are STILL LR-informed (`_engine='lr_v1'` on 10/15 games today)
- But decisions were made with STALE (median-imputed) LR values
- **At least 2-3 games/day where PRIME calls got demoted to COVERAGE unnecessarily**
- Some games that should be PRIME override get treated as coin-flip demotes

## Has it been profitable regardless?

**Yes** — 30-day MLB Sharp Card:
- Sides: 93-79-5, +6.1u, +2.3% ROI, 54.1% hit
- Props: 300-107-0, +148.2u, +27.6% ROI, 73.7% hit

Props are massively saving the record. Sides barely positive lifetime, recently down (-6.8u last 7d).

## The fix

**Add an LR-only recompute step at the 2pm ET cron (18:00 UTC), AFTER close lines lock.**

Options:
1. **Cheapest** — extend `recompute_primary_play.py` to force-re-run LR override on all games with `_engine='lr_v1'` AND `_lr_ml_shadow.p_home_win == 0.4076` (the stale-features fingerprint)
2. **Proper** — add a check in `apply_ml_lr_override` that skips override if `ctx.close_locked_at is None` (i.e., close lines haven't locked yet). Then let 2pm cron do the LR pass.
3. **Universal** — add `_lr_computed_at` timestamp to primary_play so downstream can tell if LR ran pre or post close-line lock.

Option 1 is a same-day cleanup. Option 2/3 is the durable fix.

## What was fixed already this session
- Watchdog false-positives (ensemble_engine_share + primary_play_stale mislabeled lr_v1 as legacy) → 7933a33f
- Kill switch on jerry_pick_scrub prose overwrite → ca82b33d
- LR gate field paths (was checking wrong field name) → 9da029d6

## What's queued
- Fix per above options
- Prop LR — `mlb_prop_logreg.json` exists but `mlb_pipeline_props` has NO LR fields at all. Prop LR isn't being written even STALE. Zero coverage right now.

## Related memory
- [[project_pipeline_overhaul_909]] — write-order collision analysis
- [[project_picks_engine_walkthrough_908]] — pick engine explainer
- [[project_potd_selection_redesign_908]] — POTD LR gate (Option A shipped, this is why it silently passed some coin-flip picks)
