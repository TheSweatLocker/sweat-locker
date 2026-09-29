---
name: project_live_track_record_614
description: Live forward-test track records (6/11-6/14) diverge from the retroactive audit. STRONG sides 1-5 since wiring. v3 model leading some nights, Jerry leading others. Tier names imply confidence the live data doesn't yet justify.
metadata:
  type: project
---

After ~4 days of live forward-testing the resolver framework, the tier names (ELITE / STRONG / LEAN / LIGHT) imply higher confidence than the live data is showing per (tier × category):

**SIDE resolver (ML/RL) live record (n=11 across 4 days):**
- STRONG: ~1-5 (small sample but concerning — retroactive audit said 64-78%)
- LIGHT: 5-3 (overperforming relative to retroactive 68%)
- The cohort engine's "loud side signal" isn't translating to live wins yet

**TOTAL resolver live record:**
- STRONG: ~5-3 (closer to retroactive 64% prediction)
- LEAN: 17-7-1-1 over 4 days (~71%, exceeding retroactive 62%)
- LIGHT: ~6-1 (overperforming small sample)

**Model accuracy is volatile by night:**
- Jerry was 92% on 6/12, collapsed to 42% on 6/14
- v3 was 60% on 6/12, hit 82% on 6/14
- v4 most stable across nights (58-75% range)
- No single model dominates — taking max-confidence model of the night is luck, not strategy

**Why:** The retroactive audit ran on historical data where the resolver's existence was implicit (we backfit it to what happened). Live forward-test is the real calibration. Tier names that read "STRONG = high confidence" misrepresent the actual hit rate — especially for sides.

**How to apply:**
- When recommending picks, weight by LIVE (tier × category) hit rate, not retroactive audit alone
- Total LEAN actually has the best live hit rate (~71%) — don't dismiss it for STRONG side picks
- Side STRONG live = 50% small sample — don't treat it as the highest-conviction tier
- PROP PRIME tier has held 60-69% live — most reliable category so far
- Fade flags (matchup-specific data the cohort engine doesn't capture) catch real losers — e.g. Skenes auto-fade, Sánchez fade flag both correct on 6/14

**Framework adjustment idea (queued, not yet built):**
Build a live track record table tracking (tier × category × date) so future recommendations weight by what's actually working forward. The retroactive audit gives a floor; the live record gives the truth.

Related: [[feedback_confidence_in_first_pass]] [[project_side_resolver_wired_611]] [[feedback_let_engine_speak]]
