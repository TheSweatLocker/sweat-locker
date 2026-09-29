---
name: project-playbook-fade-broken-824
description: "🚨 8/24 discovery: prop_playbook FADE side is BROKEN — loses at every tier, gets worse with higher confidence (30d n=399, 47.9% hit / -34u). BACK side works (57% PRIME, 56% STRONG)."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-09T17:00:40.399Z
---

**8/24 finding via 30d grading table backtest (n=1611 total, 399 FADE, 1050 BACK).**

## The data

| Side · Tier | W-L | Hit% | Units P/L |
|---|---|---|---|
| BACK PRIME | 39-29 | **57.4%** | **+6.49u** ✅ |
| BACK STRONG | 126-100 | **55.8%** | **+14.66u** ✅ |
| BACK LEAN | 326-314 | 50.9% | -17.34u |
| FADE PRIME | 13-14 | **48.1%** | **-2.17u** ❌ |
| FADE STRONG | 46-51 | **47.4%** | **-9.14u** ❌ |
| FADE LEAN | 91-111 | **45.0%** | **-28.19u** ❌❌ |

**FADE by edge_pp band (smoking gun — higher "confidence" = worse):**
- edge 0-10pp: 54.8%
- edge 10-20pp: 39.6%
- edge 20-30pp: 40.6%
- edge 30+pp: 36.4%

FADE at ANY confidence is a losing wager. Higher-confidence FADE calls perform WORSE than coin flip. That's not miscalibration — that's an inverted signal.

## What's been fixed
- 8/24 `b09e2e1e` — ladder direction gate hard-blocks FADE qualification
- 8/24 `resolveTier` in app/index.tsx — playbook_side='BACK' required to lift a legacy prop's tier via playbook (FADE no longer promotes to PRIME/STRONG on the wrong direction)

## Root cause FOUND + FIXED 8/24 evening (commit 7f17b63e)

`_compute_edge_and_rec` in prop_ensemble_scorer.py read the wrong-side
book odds on FADE decisions. Code assumed `prop.direction` = "what
we're betting" but for FADE we bet the OPPOSITE direction.

Effect: `market_implied_prob` and `edge_pp` for all FADE rows in
prop_playbook_decisions were computed vs the FADED (unrated) direction's
odds. When the faded direction is a longshot dog, reading its low
implied probability inflates edge_pp massively — but we're actually
betting the heavy fav on the OTHER side with tiny real edge.

This perfectly explains the inverted correlation between confidence and
hit rate — it was a math artifact, not a signal quality problem.

## Post-fix TODO
- Wait 7-14 days of post-fix FADE grading data (fresh rows with correct
  edge_pp) to know if FADE actually has edge with proper math
- If confirmed edge → un-block consumers (ladder direction gate,
  resolveTier FADE-block) that were band-aids around this bug
- Historical FADE rows in prop_playbook_decisions have polluted
  edge_pp — filter to `graded_at >= 2026-08-24` when doing signal
  reweight analysis
- Consider regrading old rows with corrected math for a cleaner
  reweight dataset

## 2026-09-09 POST-FIX 16-DAY RESULTS (n=1000 across 8/24-9/8)

| Side · Tier | W-L | Hit% | Δ vs 8/24 memory |
|---|---|---|---|
| BACK PRIME | 31-26 | 54.4% | -3.0pp |
| BACK STRONG | 71-58 | 55.0% | -0.8pp |
| BACK LEAN | 68-87 | 43.9% | **-7.0pp** ⚠️ |
| **FADE PRIME** | 7-5 | **58.3%** | **+10.2pp** ✅ |
| **FADE STRONG** | 53-40 | **57.0%** | **+9.6pp** ✅ |
| FADE LEAN | 69-82 | 45.7% | +0.7pp ❌ still bad |

FADE by |edge_pp| band (was monotonically worse with higher confidence):
- 0-10: 51.6% (was 54.8%)
- 10-20: 43.4% (was 39.6%)
- 20-30: **57.7%** (was 40.6%) — huge lift
- 30+: 44.0% (was 36.4%) — small n=25

**Read:** the math fix WORKED for PRIME + STRONG (both now beat coin flip
by 7-10pp — matches BACK-side quality). FADE LEAN is still bad (~46%) and
BACK LEAN cratered (-7pp) — the whole LEAN tier looks weak now, not just
FADE. That's a separate calibration problem, not the same bug.

## Kill-switch decision (2026-09-09)

- **Lift** the resolveTier + ladder blocks for FADE PRIME + FADE STRONG
  once client 1.0.1 ships (blocked on Apple review right now anyway).
- **Keep** the block on FADE LEAN — 45.7% × N=152 says LEAN FADE isn't
  earning a slot yet. Also matches BACK LEAN weakness.
- No historical regrade needed; fresh 16-day data is decisive.

## Still-standing kill switches (safe to keep until fresh data validates)
- 8/24 `b09e2e1e` — ladder direction gate hard-blocks FADE qualification
- 8/24 `ffeeca9f` — resolveTier in app blocks FADE tier lifts

## How to apply
- When adding new consumers of playbook_tier, ALWAYS check playbook_side. Never treat playbook_tier as authoritative without side.
- When investigating tier mismatches, check whether playbook FADE is silently promoting wrong-side tier
- FADE-mode signal decisions should be treated as SHADOW ONLY until root cause understood

## Related
- [[project_prop_playbook_port_817]] — original shipping doc
- [[project_playbook_shadow_tracking_820]] — shadow tracking pattern (this finding is why shadow tracking matters)
- [[project_playbook_reweight_821]] — reweight cycle that should catch this next iteration
