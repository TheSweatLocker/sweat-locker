---
name: project-ne-sea-wk2-read-909
description: 🎯 Wk2 TNF NE @ SEA 9/10/2026 Wed 8:20 ET — my playbook read + model + money flow + cohort tags. Review post-game to grade the process.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-09T22:11:21.772Z
---

Wk 2 NFL — NE @ SEA · Wed 2026-09-10 8:20 PM ET (KO 00:20 UTC 9/10)
Saved to review post-game vs my process playbook recommendation.

## Market close (at read time)
- Spread: NE +3.0 / SEA -3.0
- Total: 44.5
- ML: SEA -170 / NE +142

## Model outputs
| Model | NE pts | SEA pts | Total | Spread |
|---|---|---|---|---|
| Sim (MC) | 21.3 | 23.5 | 44.87 | -2.2 (SEA) |
| Panel | 22.4 | 22.0 | 44.38 | +0.5 (NE) |
| **Projected** | | | **44.5-44.9** | **+1.58 NE** |

## Primary play at read time
- side: UNDER 44.5
- tier: **COVERAGE** · conviction 53
- Why low conviction: model total (44.87) within 0.4 pts of market 44.5 — no meaningful edge

## Signal confluence: net +1 (weak)
- hfa: HOME (SEA)
- cpoe: AWAY (NE)
- def_splash: HOME (SEA)

## Money flow (cleatz only — 1 source, not triple-confirmed)
- ML: 56% bets AWAY (NE), 71% handle AWAY (NE) — **15pp sharp divergence toward NE**
- Total: 56% bets OVER, 61% handle OVER — modest 5pp OVER

## Cohort tags
- `nfl_home_fav` (SEA is home fav)

## Team stats (both 2025 season — no 2026 stats ingested yet)
| | NE | SEA |
|---|---|---|
| Pass yds/g | 262 | 239 |
| Rush yds/g | 129 | 123 |
| Def PPG allowed | 17.9 (elite) | 16.9 (elite) |
| Def pass yds/g allowed | 211 (top-10) | 236 (mid) |

## My playbook recommendation

**Best bet: NE +3.0** (if betting anything)

Reasoning:
1. Projected spread +1.58 NE vs market -3.0 = **4.5pt disagreement** — the real edge, not the total
2. Sharp money on ML (15pp handle/bets divergence) confirms
3. NE defense allows only 211 pass yds/g (top-10) — good matchup vs SEA
4. UNDER 44.5 is app primary but 0.4pt edge = pass. Nothing to trade.

Alt: PASS entirely — low-edge opener with elite defenses, save conviction for Sunday.

## Grading rubric (post-game)

- Did NE cover +3? (Best-bet call)
  - NE loses by 3 or fewer, OR NE wins outright = **WIN**
  - NE loses by 4+ = **LOSS**
  - NE loses by exactly 3 = PUSH
- Did UNDER 44.5 hit? (App primary play, low conviction)
  - Total ≤ 44 = WIN
  - Total ≥ 45 = LOSS
  - Total = 44.5 = PUSH
- Did sharp money read hold? (NE ML at +142)
  - NE wins outright = HIT
- Actual score for records:
  - NE __ - __ SEA
  - Result: [TBD]

## Post-game review notes (fill after game)

- [ ] Actual score
- [ ] NE +3 covered? Y/N/Push
- [ ] UNDER 44.5 hit? Y/N/Push
- [ ] NE ML upset? Y/N
- [ ] Model vs actual delta
- [ ] Sharp money read validated? Y/N
- [ ] Confidence in the projected-spread edge (repeatable pattern or noise?)

## Process notes

- Blend label was NOT live on this ctx row at read time (nfl_game_context upsert failed on write-path ConnectionResetError). Team stats shown are 2025 season pure, which is correct for this game (both teams have 0-1 games this year).
- Data quality: `sources_present=['cz']` only — fadereport last 9/3, so no triple-confirmed check possible.
- If NE +3 hits and process reasoning was right → save as a template ("projected spread ≥ 4pt disagreement with market" as a signal to weight higher than UNDER low-conviction).
- If NE +3 fails → note whether it was a coverage variance loss or the projected-spread signal itself is unreliable at this sample.
