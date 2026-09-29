---
name: audit-battery-721-findings
description: "5-audit battery run 2026-07-21 after 46% card night. Root causes found, patches shipped for projected_hits + Panel-drop-from-totals. Cohort discovery + form/recency + external lens accuracy tracked."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  audit_date: 2026-07-21
  modified: 2026-07-21T15:52:09.756Z
---

**Ran 2026-07-21 after 46% card + user demanded "do all fixes."**

## Audit 1 — projected_hits ROOT CAUSE (SHIPPED)

`projected_hits = raw mean of last 7 dated starts`. No regression, no opp/park adjustment. Explains 5-for-5 HA UNDER losses on 7/19-7/20 (pitchers on hot L7 streaks getting auto-PRIME on the "hot streak illusion").

**Patch shipped:** `_blended_projected_hits()` in generate_props.py — season BAA-from-xERA baseline + L7-divergence shrinkage + opp wRC+ multiplier + park factor multiplier. Applied to both score_pitcher_ha_over + score_pitcher_ha_under.

**Validation plan:** shadow mode 5 days, target UNDER over-optimism drops from ~65% to ≤55%.

## Audit 2 — External lens accuracy 7/19-7/20

**BOOST (real signal):**
- Dimers ≥60% win prob (100% n=3)
- CBS staff picks / Selesnick (75% n=4)
- Public bets ≥70% AND price ≤−150 (4-0 chalk-only lens)
- Action sharp $ money gap ≥ +35 (TB +40 hit)

**FADE (2-day directional flag, needs 5-day continuation):**
- ❌ Ballpark Pal wind: 0-3
- ❌ Pickswise 5-STAR: 0-2 (contrarian signal)
- ❌ Action sharp $ gap +15 to +34 (1-3 — mid-sharp trap zone)

**NEUTRAL (coinflip aggregate):** Pickswise 3-star + VSiN + BettingPros + Doc Sports + PickDawgz = 52.9% n=17. Do NOT treat "3 handicappers agree" as anchor.

**Ship actions (pending):** suppress Ballpark Pal wind from Jerry reads; add Pickswise 5-star + Action $ mid-gap fade tags.

## Audit 3 — Cohort discovery (12 new + 5 counterintuitive)

**TOP priority:** `line_move_sharp_disagreement` (reverse-line-move) — industry's biggest single signal, we don't mine it yet. ~300+ games/season.

**HIGH:** rest_mismatch_edge, first_inning_shaky_x_cold, wrc_l14_drift_gap, bp_taxed_x_close_game_risk.

**Counterintuitive worth backtesting:** west-coast trip G3+ UNDER · chalk ML after 10-run shutout FADE · taxed pen × elite SP = MORE UNDER · line-moved-to-public-side FADE · both-1st-inn-clean YRFI.

## Audit 4 — Panel side vs total (SHIPPED)

**30d (n=254):**
- Panel sides: 56.0% overall, **63-64% at |margin|≥1.0** ⭐ real edge
- Panel totals: 51.2% overall, **43.2% when loud (gap≥2)** ← worse than coinflip
- 29.6% of games had Panel off by ≥5 runs — bimodal / fat tail

**Patch shipped:** `tier_discipline_gate.py::evaluate_total()` — removed Panel-NEU coinflip trap, Panel-disagree LEAN flip, and 4-way ELITE Panel boost blocks. Panel signal on totals is dead; the 2026-06-24 "Panel beats Composite 54-46" backtest was small-sample tail variance.

Panel retained on sides (`evaluate_ml`) where the 63-64% edge holds.

## Audit 5 — Form/recency cohorts (redirected from weather)

20 cohorts focused on hot streaks × context:

**HIGH:** hot_bats_on_road (fade OVER) · hot_bats_vs_ace_regression · momentum_vs_momentum_pitcher_wins · both_teams_hot_over_trap (contrarian UNDER) · heating_l20_cold_l10_bounce · team_stepping_up_l20 · underperforming_pythag_hot.

**MED:** sp_hot_vs_scuffling_offense · hot_offense_missing_star (injury summary) · bp_bleeding_vs_hot_offense · pitching_staff_leaking_l10 · home_cold_wake_up_return · hot_bats_vs_new_hand (platoon regression) · hot_offense_pitchers_park · line_moves_toward_cold · hot_offense_vs_fresh_hot_bp.

**Market-overweights-recency fades:** blowout-inflated L5 · both-hot double-count · hot-into-ace · hot-bats-traveling · hot × heating pitcher.

## Shipping order

1. ✅ `_blended_projected_hits()` — SHIPPED to generate_props.py
2. ✅ Panel drop from totals gate — SHIPPED to tier_discipline_gate.py
3. Pending: Ballpark Pal wind suppression in Jerry reads
4. Pending: fade tags for Pickswise 5-star + Action $ mid-gap
5. Pending: new cohort features (line_move_sharp_disagreement first)
6. Pending: sub-band prop_edge_calibration migration
7. Pending: POTD universal pool wire-in

## Related

- [[project_hits_allowed_calibration_719]] — original audit chain
- [[project_ha_under_conviction_band_720]] — 85+ conv band finding
- [[project_30d_lens_audit_718]] — Panel first anomaly flag
- [[project_composite_debias_finding_712]] — jerry debias precedent
- [[project_potd_universal_pool_720]] — POTD architecture change
