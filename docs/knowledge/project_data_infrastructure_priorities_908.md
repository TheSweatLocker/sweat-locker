---
name: project-data-infrastructure-priorities-908
description: "🎯 9/8: TOP PRIORITY STACK — (1) LR for all sports, (2) grading fixed for all sports, (3) daily routine automation. Everything else queues behind this until the foundation is solid."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T13:37:54.417Z
---

**User directive · 2026-09-08 morning post-audit.** After spotting LR at 73% MLB, then discovering grading gaps in NCAAF (55/70 ungraded due to ncaaf_odds_pull date bugs), user declared this the operating priority stack for the next work session(s):

## Priority 1 — LR for all sports

**Why:** MLB LR-endorsed picks: **73.5% hit rate over 66 picks in 14 days.** Shadow-disagreement flip captures 2+u/game when we listen. LR is the strongest single signal in the stack. Expanding it cross-sport is the biggest EV lever we can pull.

**Current state by sport:**
- **MLB** ✅ LR firing on ~90% of games (7 of 11 games yesterday had `_lr_p_home_win` or `_lr_p_over`)
- **NFL** 🚨 LR NOT firing — signals empty on prop-side (`_lr_tier_raw: None`, coverage 0%). Need to populate NFL prop signals per [[project_nfl_prop_signal_gap_908]]. Game-side LR status unknown — need audit.
- **NCAAF** 🚨 LR NOT firing at all — no signals coverage. Need model retrain + signal population.
- **NBA / NHL / NCAAB / UFC** — LR status unknown. Need audit.

**Concrete work:**
1. Audit LR field-population per sport: which sport's game_context / prop rows have `_lr_*` fields populated vs null
2. For sports where LR fields are missing: identify which upstream script should be writing them
3. Ensure LR trains on graded outcomes for each sport (requires grading to work — see Priority 2)
4. Promote LR shadow signals to composer level: if LR STRONG-disagrees with pipeline pick, don't publish above LEAN

## Priority 2 — Fix grading for ALL sports

**Why:** LR needs graded outcomes to train. Users need graded picks to trust records. Sharp Card / Steam Room / POTD / Ladder all depend on graded results for their P/L display. Every sport that isn't grading properly = compounding stale data + broken model retraining loop.

**Current state by sport (per 9/8 audit):**
- **MLB** ✅ 100% graded overnight (11/11 games, 71/71 prop reads, 96/96 pipeline props). Grader + resolver both fire cleanly.
- **POTD** ✅ Now graded (grader shipped 9/8 in `grade_potd.py`). Wired into overnight workflow. 7-day backfill: 5W-2L.
- **NCAAF** 🚨 Only 15/70 Sat 9/6 graded even after 3-pass resolver improvements. Root cause: `ncaaf_odds_pull` is tagging future games with wrong game_date. See [[project_ncaaf_grading_gap_908]].
- **NFL** 🚨 Status unknown post-Week 1 — need audit. Some grading exists but not verified end-to-end.
- **NBA / NHL / NCAAB / UFC** — Status unknown offseason. Need audit BEFORE their seasons start (NCAAB 11/3, NBA 10/24, NHL Oct).

**Concrete work:**
1. Fix `ncaaf_odds_pull.py` date-truncation bug (games showing wrong game_date)
2. Backfill script to re-date existing NCAAF jerry_reads using CFBD scheduled dates
3. Audit NFL / NBA / NHL / NCAAB / UFC grading end-to-end
4. Ensure every sport has:
   - Post-game results table populated (via UPSERT, not PATCH-only)
   - Grader that reads results table + updates jerry_reads.result
   - Prop grader for sports with props (MLB done, NFL props needs audit)

## Priority 3 — Daily routine process (automation + verification)

**Why:** User is currently having to manually ask "did grading happen? are picks graded? is Steam Room accurate?" every morning. That's a symptom of unreliable automation. The goal: overnight jobs run reliably + a single-command morning verification catches any misses.

**Current state:**
- Overnight workflow (`mlb_grade_overnight.yml`) runs but has silent failures (POTD wasn't wired, NCAAF resolver ineffective, no cross-sport coverage check)
- No morning verification script — user has to ask Claude every day
- Morning brief spec exists ([[docs/MORNING_BRIEF.md]] — shipped 9/8) but the RUNNABLE version doesn't exist yet

**Concrete work:**
1. Ship `docs/scripts/morning_brief.py` — a single-command script that:
   - Runs each Section 1 completeness check (grading rowcounts per sport)
   - Exits non-zero if ANY sport is <80% graded
   - Prints the Section 2 pick record + Section 3 Steam Room P/L
   - Prints Section 5 regressions (Peterson orphans, MoneyFlow field mismatch, etc.)
2. Wire this script to fire at end of overnight workflow AND email user if it exits non-zero
3. Ship `docs/scripts/nightly_audit.py` — expanded overnight version that also runs the graders it detects as missed, so morning brief is always green
4. Cross-sport audit dashboard in `jerry_cache` (surfaces overnight metrics for review)

## Sequencing (recommended order)

Because grading feeds LR training, and LR feeds picks, and picks feed records — the order matters:

**Week of 9/8-9/13:**
1. Fix NCAAF grading (ncaaf_odds_pull date bug + backfill) → **BEFORE Sat 9/13**
2. Fix NFL prop signal population → **BEFORE Sun 9/14 kickoff**
3. Ship `morning_brief.py` runnable → **so 9/14 morning is measurable**

**Week of 9/15-9/20:**
4. Audit LR field population across all sports
5. Audit grading end-to-end for NBA / NHL / NCAAB / UFC (before their season starts)
6. LR-endorsed subset surface record (new field on `surface_records`)

**Week of 9/21-9/27:**
7. Promote LR STRONG-disagree gate on Sharp Card composer
8. Cross-sport LR shadow surfacing in Game Detail

## Related memories
- [[project_ncaaf_grading_gap_908]] — NCAAF-specific fix with the ncaaf_odds_pull date bug now identified
- [[project_nfl_prop_signal_gap_908]] — NFL prop signal population gap
- [[project_lr_shadow_promotion_907]] — LR shadow → composer gate promotion spec
- [[docs/MORNING_BRIEF.md]] — daily routine spec that this priority stack drives

## Non-goals (until foundation solid)
- Website content polish (queued in [[project_website_update_queue_908]] — comes after)
- Any new feature work
- Any new prop types or lens additions
- Any UX refactoring on already-working surfaces

**The bar: no new features until every sport grades reliably + LR fires per sport + morning brief is a one-command green check.**
