---
name: hits-allowed-projection-calibration-719
description: "7/19 grading revealed 3/3 pitcher HA UNDERS lost badly despite 1.4-2.2 hit projected cushions. Investigation queued: is projected_hits under-forecasting systematically?"
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Set 2026-07-19 after grading Grinder's card 1-3.**

## The signal

3 pitcher HA UNDERs recommended, all 3 lost badly:
- Yesavage U 4.5 (proj 3.1) — actual 5H · miss 1.9
- Griffin U 6.5 (proj 4.3) — actual 8H · miss 3.7
- Holmes U 5.5 (proj 3.6) — actual 7H · miss 3.4

All had 1.4-2.2 hit projected cushions and pipeline PRIME/STRONG tags. Every one landed OVER by 1-3 hits.

## Contrast (worked):

- Eury Pérez HA U 4.5 PRIME 86 (proj 3.3) — I FADED, actual 1H (would have won)
- Foster Griffin BB U 1.5 (proj 0.7) — actual 1BB → hit but tight

## Hypothesis

`projected_hits` calculation may be under-forecasting for:
- Pitchers coming off L3 hot stretches (Griffin L3 ERA 0.95, Holmes L3 ERA 1.23) — regression risk
- 6.5+ hits lines where cushion looks generous but BABIP variance eats it
- Extended-outing projections (17+ outs) where 6th+ inning hits pile up

## Action queued

1. Audit last 30d `player_name` hit props: compare `projection` vs actual for `ha_under` and `ha_over`
2. Segment by proj cushion band (0-1, 1-2, 2+)
3. Check if L3 ERA vs season ERA gap predicts hits over-performance vs projection
4. If systematic under-forecast → recalibrate projected_hits formula or add L3-hot regression term

## Related

- [[project_30d_lens_audit_718]] — total lens audit found similar calibration issues
- [[project_composite_debias_finding_712]] — jerry needed −0.62 debias for totals
- [[feedback_card_process_discipline_718]] — juice check especially matters if projections are systematically off
