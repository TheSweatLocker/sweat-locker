---
name: potd-universal-pool-720
description: "POTD candidate pool = ALL high-conviction plays (props/sides/totals) across ALL sports. Today's REST DAY was wrong — Weathers/Springs/Bibee should have surfaced. Architectural change queued pre-launch."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-20T21:00:22.281Z
---

**Set 2026-07-20 after 2nd consecutive REST DAY POTD despite loud pipeline conviction on props.**

## The problem

Current [play_of_day.py](mlb_pipeline/play_of_day.py:2919) builds ONE candidate per GAME (MLB + NBA). Each candidate carries `dim_prop_score` but props are NOT standalone POTD candidates. `build_lean()` prioritizes side/total/NRFI leans; props ride shotgun.

Result: today, pipeline had 4 loud PRIME plays (Springs 100 ER, Bibee 86 HA, Misiorowski 86 outs, Weathers 68 outs @ 92-97% class rate) and POTD still fired REST DAY because no game's SIDE or TOTAL cleared the tier gate.

## User decision (2026-07-20)

**POTD candidate pool = highest-conviction play across all surfaces, cross-sport.**

- Props compete as first-class candidates
- Sides compete as first-class
- Totals compete as first-class
- ALL sports feed the same pool (MLB, NBA, NHL, NCAAF/B, UFC, etc.)
- The single "loudest" conviction play wins POTD

If the pipeline generates a play with real conviction — no matter what type or sport — it should surface as POTD. REST DAY should be rare (truly nothing meets the bar), not a default when side/total is contested.

## Ranking dimension (proposed)

Score each candidate on:
1. **Real hit-rate expectation** — pipeline conviction × class calibration % (e.g., outs_under PRIME = 92-97% class rate is a hard anchor)
2. **Edge vs juice** — projection cushion / breakeven % (juice check per [[feedback_card_process_discipline_718]])
3. **Sample size** — minimum n on the class calibration; downgrade thin samples
4. **Cross-signal confluence** — number of independent lenses agreeing (already tracked per game — extend to props)

Rank composite: `hit_rate_pct × cushion × cross_signal_weight` — juice as tiebreaker at the top.

## Cross-sport application

- MLB: props (K/BB/H/Outs/ER), sides, totals, NRFI/YRFI, batter props
- NBA: player props (PTS/AST/REB), sides, totals — same pool
- NHL: same when active
- NCAAF/B: sides + totals only (per [[project_ncaab_scope]] / [[project_ncaaf_scope]])
- UFC: fight winners, method, rounds (from [[project_ufc_328_calibration]])

If today's UFC card generated a PRIME fight winner, it should compete against MLB props for POTD.

## Implementation sketch

**Step 1** — factor out candidate construction into a `build_potd_candidates()` function that takes ALL surfaces:
```python
def build_potd_candidates(sports=('MLB','NBA','NHL','NCAAB','NCAAF','UFC')):
    candidates = []
    for sport in sports:
        # Game-level side/total plays
        candidates.extend(_game_level_candidates(sport))
        # Prop-level plays
        candidates.extend(_prop_level_candidates(sport))
    return candidates
```

**Step 2** — add a `PropCandidate` shape that carries:
- player, prop_type, line, direction
- pipeline_conviction, tier
- class_hit_rate (from `prop_edge_calibration` table)
- book_odds, breakeven_pct
- projection, cushion
- cross_signal_count

**Step 3** — rank composite scoring function that respects the different play types (game-side vs prop) using a unified expected-EV metric.

**Step 4** — POTD selector picks argmax(composite_score) across the unified pool. Only fire REST DAY if the top composite falls below a floor threshold (e.g., composite < 60 = truly nothing worthwhile).

## Why this matters pre-launch

Users see REST DAY on a day where pipeline had PRIME 100 and PRIME 86 props → looks broken, not disciplined. The point of POTD is "we have conviction on this today." If we do, we surface it. If we don't, we sit out. Currently we sit out when we shouldn't.

## Related

- [[project_30d_lens_audit_718]] — gate rewrite already queued; this addresses the "gate starving POTD" side of it
- [[project_potd_tier_gate_shipped_624]] — original tier discipline gate
- [[project_side_resolver_wired_611]] — resolver architecture
- [[project_prop_edge_calibration_july]] — prop_edge_calibration table already has class hit rates ready for use
- [[feedback_card_process_discipline_718]] — juice check + tier discipline principles apply to unified pool
- [[project_cohort_engine_universal_architecture]] — this is the same "universal signal, sport-specific inputs" pattern
