---
name: miller-k-prop-postmortem-725
description: "Bryce Miller K over 5.5 was 99-conv PRIME 7/24 and returned 0 K's in 5.1 IP. Pipeline had whiff_rate=10% + L3 K% down 7.7pts vs season but didn't use either signal. Fix: add whiff + L3-vs-season gates to K-over prop scoring."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-25T19:36:14.862Z
---

**Set 2026-07-25 after 7/24 Miller K over 5.5 (99 conv PRIME) returned 0 K's.**

## Actual result
- 89 pitches / 60 strikes = 67.4% strike rate (decent)
- 5.1 IP, 6 H, 5 ER, 1 BB, **0 K**, 2 HR
- 13 airOuts / 3 groundOuts — contact-heavy, flyball-heavy start
- Batters made contact + lifted the ball. Not wild — just no swings-and-misses.

## Pre-game signals USED in 99-conv rating (all bullish)
- xera 2.49 (elite)
- season k_pct 29.5%
- l5_confirm avg 7.0 (4-of-5 over 5.5)
- k_gap +4.4 vs lineup
- bucket_k 35%, bucket_sustain 26%

## Pre-game signals AVAILABLE but IGNORED (would have caught it)
- **whiff_rate: 10.0%** — very low; elite K pitchers are 28-32%. Season
  K% of 29.5 does NOT cohere with whiff of 10 — one of them is stale
  or the K% is inflated by early-season noise. Pipeline trusted K%.
- **last_3_k_pct: 21.8%** — down 7.7 pts from season 29.5%. That's a
  meaningful regression signal that we track but don't use.

## Fix queued

Two new gates for K-over prop scoring in generate_props.py:

### Gate A: whiff_rate credibility check
    if pitcher_stats.get('whiff_rate') is not None:
        whiff = float(pitcher_stats['whiff_rate'])
        if whiff < 15.0:
            # Contact pitcher — K prop is a trap regardless of season K%
            conviction *= 0.6  # or SUPPRESS entirely
            signals['whiff_gate'] = f'whiff_rate {whiff}% too low for K-over — DOWNGRADED'

### Gate B: L3 vs season K% divergence
    season_k = pitcher_stats.get('k_pct')
    l3_k = pitcher_stats.get('last_3_k_pct')
    if season_k and l3_k:
        delta = float(l3_k) - float(season_k) * 100  # normalize
        if delta <= -5:
            conviction *= 0.75
        if delta <= -8:
            conviction = 0

### Combined
When BOTH gates fire: SKIP tier automatically. This would have caught
Miller (whiff 10 + L3 delta -7.7) as SKIP not PRIME 99.

## Broader implication for prop pipeline

The 99-conv score is a WEIGHTED SUM of season averages. Should be a
DEBIASED score that penalizes divergence between:
- Season vs L3/L5 form
- K% vs whiff_rate consistency
- Any pitcher-quality metric that's structurally inconsistent

Queue: audit ALL K-over PRIMEs from last 30 days for whiff/L3 divergence
patterns to confirm this isn't a one-off Miller quirk.

## Related

- [[project_prop_edge_calibration_july]] — prop pipeline recal
- [[project_prop_jerry_odds]] — prop tier gating
- [[feedback_confidence_in_first_pass]] — honest confidence
