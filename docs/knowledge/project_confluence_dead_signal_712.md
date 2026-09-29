---
name: confluence-net-dead-signal-july-2026
description: signal_confluence_net has no predictive power for total direction over 30d audit (n=397); flat 44-52% OVER rate across all confluence tiers
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Discovered 2026-07-12 during fix-round audit L.** 30-day rolling audit of `signal_confluence_net` vs actual OVER/UNDER outcomes on `close_total` (n=397 games from mlb_game_results).

## Data

| net | n | OVER% |
|---|---|---|
| -4 | 6 | 33% |
| -3 | 17 | 53% |
| -2 | 26 | 48% |
| -1 | 30 | 61% |
| 0 | 45 | 35% |
| +1 | 49 | 51% |
| +2 | 61 | 37% |
| +3 | 44 | 49% |
| +4 | 36 | 44% |
| +5 | 30 | 57% |
| +6 | 20 | 50% |

**Rolled up:**
- net >= +3: OVER rate 49.6% (n=139) — coinflip
- net <= -3: OVER rate 51.9% (n=27) — coinflip
- net -2..+2: OVER rate 44.3% (n=201) — slight UNDER lean but noisy

## Verdict

**KILL.** No monotonic relationship. tier_discipline_gate.py comment (line 40) already says "IGNORE: signal_confluence_net. Walk-forward shows 0.9% feature importance + no monotonic predictive relationship." Now confirmed on rolling 30d live data.

## How to apply

- Do NOT weight confluence net in future card recommendations.
- Do NOT surface as a driver in Jerry writeups.
- Queue for UI removal: drop from `sweat_breakdown.confluence_net` display in app.
- Related tier gate + POTD selector already ignores it — no code changes needed there.

## Related

- [[project_re_weight_model_votes_609]] — same theme, older Jerry tot band recal
- [[project_may17_confluence_audit]] — earlier finding on confluence PRIME drop
