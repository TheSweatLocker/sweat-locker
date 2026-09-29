---
name: prop-edge-calibration-july-2026
description: Prop pipeline recalibration plan. 33-day backtest shows allow-list of proven-edge buckets delivers +10.7pp lift over raw.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Discovered 2026-07-05 during 5-day prop audit.** Original take that "STRONG tier is broken" (20% + 27% on 7/3 + 7/4) was misleading — that was recent variance. 33-day full backtest (n=568) shows STRONG at 54% overall (near coinflip, not broken).

**Real edge lives at (tier, prop_type, direction) bucket level, not tier-wide.**

## KEEP Buckets (≥60% historical, n≥5)
- STRONG bb_under (under): 74% (n=35) ⭐⭐
- PRIME ha_over (over): 70% (n=20)
- STRONG ha_over (over): 65% (n=23)
- PRIME ks_under (under): 62% (n=16)
- PRIME outs_under (under): 67% (n=6)
- PRIME ha_under (under): 60% (n=55) — biggest reliable sample

## KILL Buckets (<45% historical, n≥5)
- PRIME ks_over (over): 36% (n=11)
- STRONG outs_over (over): 20% (n=5)
- STRONG er_over (over): 41% (n=17)
- STRONG ha_under (under): 45% (n=33) — marginal

## NEUTRAL/Coinflip (not KILL, not real edge)
- PRIME bb_under: 54% (n=70) — most-often-picked bucket but coinflip
- PRIME er_over: 53% (n=30)
- STRONG bb_over: 52% (n=94)
- STRONG ks_under: 53% (n=51)

## Backtest Results
Rule D (allow-list ≥60% buckets only) over 33 days:
- PRIME RAW 56% → FILTERED 63% (+6.5pp, n=266 → n=97)
- STRONG RAW 54% → FILTERED **71%** (+16.7pp, n=302 → n=58)
- Combined: 55% → 66% (+10.7pp)

Volume cut ~72% (568 → 155) but surviving picks are true edges.

## How to apply
- Wire `prop_edge_calibrator.py` (nightly job) + `prop_edge_calibration` table + integration in `generate_props.py` before prop tier assignment.
- Dry-run mode first — log what WOULD be filtered without applying.
- Weekly backtest (`backtest_prop_edge_filter.py`) writes to `prop_edge_backtest_history` for tracking calibration drift.
- Ship as v1.1 (post-launch). See [[project_launch_priorities_july]].

## Related
- [[project_props_pipeline_pivot]] — original pipeline conversion from EV scanner
- [[project_under_props_calibration]] — earlier calibration attempt (K/hits Under)
- Supersedes the "STRONG prop tier calibration" HIGH todo — reframe as "prop edge calibration"

## Sample setup
- STRONG bb_under HAS n=35 and 74% hit — sturdiest edge. Filter should protect it.
- PRIME bb_under has n=70 but only 54% — biggest volume of "coinflip PRIME picks" that appear sharp but aren't.
- Casey Mize BB UNDER 1.5 at PRIME 97 fell in PRIME bb_under bucket (54%) — good example of "high conviction on coinflip bucket" that filter would downgrade.
