---
name: feedback-potd-juice-gate-803
description: "POTD max -250 ML juice (loosened from -200 on 9/12 with LR ≥ 0.60 guardrail); heavy-fav ML still often anti-consensus trap"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-17T18:24:29.903Z
---

**Rule (current, per code):** POTD picks skip ML at **-250 or juicier**.
Auto-filtered in `jerry_anchor_potd.py:590`. Gate was **loosened from -200
→ -250 on 2026-09-12**, coupled with the LR ≥ 0.60 guardrail downstream.

**Why loosened:** the -200 cap was tossing legit PRIMEs like Brewers ML
conv 100 @ -207 (won 9/11, LR 1.00) and Dodgers ML conv 91 @ -209 (won,
LR 0.91). At -250 implied prob = 71.4% — pairing with LR ≥ 0.60 keeps
EV positive: 71% market vs 60%+ model = -11pp minimum edge. Anything
below -250 (e.g. -300 implied 75%) would need LR ≥ 0.75 to have EV,
which the 0.60 gate can't guarantee, so the hard cap stays at -250.

**Verified 9/17:** Padres ML @ -203 with LR 0.93 → passes gate,
serves POTD. 26pp edge on the LR reading, honest edge above the juice.

**Original 2026-08-03 rule (superseded but archived):** POTD picks NEVER
include ML at -200 or juicier. User directive: "-200 or juicier ML
shouldn't be POTD they are juiced up and in my opinion often fall into
anti-consensus plays." Corroborated by:

**Why**: 2026-08-03 user directive: "-200 or juicier ML shouldn't be POTD they are juiced up and in my opinion often fall into anti-consensus plays." Corroborated by:
- [[feedback_heavy_fav_ml_trap_803]] — heavy-fav ML underperforms implied win rate at every juice bucket (-13.2% ROI at -200 to -249 bucket)
- POTD as marketing anchor deserves +EV plays, not coin-flip winners at juice tax
- 8/2 Brewers -232 POTD lost outright (Angels won 3-0) — the pattern that surfaced this rule
- 8/3 Yankees -217 would have been POTD, but Cards jumped to 3-0 early — same trap pattern

**Implementation**: `jerry_anchor_potd.py` — after conviction ≥70 filter, adds ML-only juice gate:
```python
for r in eligible:
    if call_market != 'ml': continue  # non-ML unaffected
    pick_ml = home_ml_close if side=='HOME' else away_ml_close
    if pick_ml <= -200: skip
```
- Non-ML plays (totals, runline, props) unaffected — they have their own juice-cap logic in `prop_tier_calibration`
- If ALL eligible reads are heavy-fav ML, falls to `_write_no_play` → discipline preserved

**How to apply**:
- Don't override the gate for a "hot" heavy-fav; the pattern says the discipline is worth more than the individual pick
- Correlated markets at fair price (total UNDER when fav is dominant SP) are the swap — see [[feedback_heavy_fav_ml_trap_803]] for the pattern
- Same rule should extend to DotD anchor when we surface a heavy-fav dawg (edge case)

Related: [[feedback_heavy_fav_ml_trap_803]], [[project_juice_fav_rl_trap_724]], [[feedback_sharp_money_discipline_802]].
