---
name: project_sweat_card_multisport_921
description: Sweat Card is MLB-primary + football appendix; breaks when MLB ends ~09-28. Generalise to sport_registry-driven. Plus tap-through to game detail.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T18:13:25.244Z
---

**Two queued items from 2026-09-21.**

## 1. Sweat Card composition after MLB ends

Current structure (generate_sweat_card.py):
- `top_8` = **MLB only** — the main card
- `football_picks` = appendix, **hardcoded to ('NFL','NCAAF')**, capped at 5
  (fetch_football_picks, line ~518)
- NBA/NHL/NCAAB are in NEITHER list

So when MLB's regular season ends (~2026-09-28), `top_8` empties and only
a 5-pick football appendix remains. NHL does not start until 10-08, NBA
10-21 — a ~10 day window where the card is nearly empty.

**Recommendation: generalise rather than add sections.** Replace
`fetch_football_picks(('NFL','NCAAF'))` with a sport-agnostic fetch
driven by `sport_registry.state == 'in_season'`, so NHL/NBA/NCAAB join
automatically when their seasons flip — no code change per sport, and it
respects [[feedback_backside_dictates_app_renders]] (server decides).

Why not one section per sport: they multiply (MLB + football + NBA + NHL
+ NCAAB = 5), and users want the best plays, not a sport taxonomy. The
card already has `active_sports` + mode detection
(thin/standard/overload) to lean on.

Open question for Andy: does `top_8` stay MLB-shaped (it carries richer
per-pick prop data) with others appended, or does everything merge into
one ranked list with sport badges?

## 2. Tap-through to game detail

From Sweat Card AND The Sharp, tapping any pick should open Game Detail
for THAT game. Currently picks are not tappable. Needs a game_id on each
card item (Sharp items carry `game_id`; Sweat Card `top_8` items carry
`source_key` which is a game_id for ML/total but a DATE for POTD/DotD —
see [[project_ncaaf_duplicate_total_lens_920]] for that shape).
