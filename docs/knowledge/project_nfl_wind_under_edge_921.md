---
name: project_nfl_wind_under_edge_921
description: "NFL outdoor games with 10-14mph wind go UNDER 59.5% (n=210, CI 53-66), 5/6 seasons. High wind 15+ is NOT significant — books price visible wind, not moderate wind. Possibly decaying; ship in shadow first."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T21:13:21.514Z
---

Found 2026-09-21 while auditing Andy's "state of the art" NFL ideas
(see [[project_nfl_sota_signal_audit_921]] — primetime and pass-D tier
were both rejected; this is the one thing that cleared).

## The edge

NFL regular-season games, outdoor roof only, 2020-2025:

    wind <10 mph     OVER 51.7% [48-56]  n=607
    wind 10-14 mph   OVER 40.5% [34-47]  n=210   <- UNDER 59.5% [53-66]
    wind 15+ mph     OVER 45.1% [36-54]  n=113   (not significant)

Season by season (UNDER%): 2020 68.8, 2021 56.8, 2022 68.0, 2023 66.7,
2024 47.5, 2025 53.3. **5 of 6 seasons UNDER won.**

Margin confirms it is a pricing error, not a grading artefact:
  * 10-14 mph games land **-0.87 pts** vs the closing total (n=210)
  * <10 mph games land **+0.89 pts** vs the closing total (n=607)
  * a 1.76-point swing the closing number does not capture

## Why moderate wind and not high wind

The counterintuitive part — 15+ mph is NOT significant — is the tell that
this is real rather than fitted. Heavy wind is visible, discussed all
week, and books shade the total for it. Moderate 10-14 mph wind does not
make the broadcast and appears not to get priced. The edge lives exactly
where attention doesn't.

## Caveats before shipping

  * 2024 (47.5%) is the only losing season and 2025 (53.3%) is soft. The
    trend 68.8 / 56.8 / 68.0 / 66.7 / 47.5 / 53.3 is consistent with
    market adaptation. Could be decay; could be two noisy seasons.
  * n=210 total is modest. CI lower bound 53% is above breakeven for
    -110 (52.4%) but not by much.
  * Requires a forecast wind value at pick time, not the post-game
    `wind` column used here. Verify the forward source agrees with what
    nfl_game_results records after the fact before trusting it live —
    otherwise this is lookahead bias.

## Recommendation

Shadow-record it first per [[feedback_suppression_gate_needs_shadow]]:
compute the flag, store it, do NOT let it move a published total until it
has forward-tested. Exit condition should be evidence-based and reachable.

If it holds, it belongs as a totals-side signal in the NFL ensemble, not
as a standalone badge.
