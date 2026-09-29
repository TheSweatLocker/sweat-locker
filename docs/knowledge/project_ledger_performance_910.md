---
name: project-ledger-performance-910
description: Ledger surface underperforming — user flagged 9/10 for a dedicated discussion. Investigate build_chalk_prop_parlay + build_chalk_parlay hit rates before v1.0.1 ship.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-10T19:33:46.273Z
---

**The Ledger hasn't been performing well.** User flagged 2026-09-10 during launch prep — wants a dedicated discussion soon.

**Why:** Ledger is one of the 4 Steam Room tabs users pay for. If its numbers are visibly bad on the receipts view, it drags credibility of the whole product. User is willing to rethink the Ledger construction rules if the data supports it.

**How to apply:**
- Do NOT make changes to the Ledger picker until we discuss.
- BEFORE the discussion, pull hard numbers: last 30d W-L per Ledger kind (chalk_parlay, teased_totals_combo, teased_spreads_combo, teaser, chalk_prop_parlay), units net per kind, hit rate per leg count (2-leg vs 3-leg vs 4-leg).
- Current construction:
  - `build_chalk_parlay` = 2-4 leg sides + total picks at chalk odds
  - `build_teased_totals_combo` / `build_teased_spreads_combo` = teased alt lines
  - `build_chalk_prop_parlay` (added 9/9) = 2-4 leg PRIME/STRONG props at published lines, target +100 to +400
- Ledger writer: `mlb_pipeline/generate_ledger.py`
- Ledger renderer: `app/index.tsx` around line 17098, KIND_META map

**Discussion questions to pre-answer:**
1. Which kind is dragging the average? (Isolate by pulling surface_records['MLB|ledger|d30'] filtered per kind.)
2. Are the teased legs the problem (teaser math too aggressive) or the chalk sides (correlated same-day loss cluster)?
3. Is the +100 to +400 target range too wide? A -150 to +150 range would flatten variance.
4. Should we drop teasers entirely and just ship chalk parlays until the teaser leg math is retuned?
5. Would a "no more than 1 leg per game" rule improve results (currently allows same-game legs when slate is thin)?

**Related memory:** [[project_ledger_teasers_817]] (original Ledger design decision to include teasers).
