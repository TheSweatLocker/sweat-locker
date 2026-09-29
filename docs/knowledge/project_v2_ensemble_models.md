---
name: v2 Ensemble Model Architecture (post-launch May/June)
description: 5-model ensemble plan to replace single-formula projections with model-level confluence
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
**Decision (2026-04-29):** Post-launch v2 of the projection layer is an ensemble of 5 independent models, each looking at the world through a different lens. Consensus voting replaces single-formula confidence.

**Models (each must use DISJOINT feature sets to avoid correlated errors):**
1. **Pitcher Quality** — xERA, K%, BB%, HR/9, last 3 starts. Ignores offense + recency.
2. **Offense Strength** — wRC+, OPS, R/G, vs-hand splits. Ignores pitchers + recency.
3. **Recency** — L10 R/G, L10 RA, L5 trend, streaks. Ignores season-long stats.
4. **Matchup / Situational** — splits vs hand, park, weather, lineup confirmed. Ignores aggregate stats.
5. **Bullpen / Late** — 7-9 bullpen ERA, gassed flag, 7-9 offense. Ignores starters + 1-3.

**Consensus engine:**
- Median of all models = robust projected_runs_home / projected_runs_away
- Agreement count = confidence tier (4-5 agree → PRIME; 3 → STRONG; <3 → flag uncertainty, lower bet size or skip)
- Variance flag = when models disagree by >2 runs, mark game as high-uncertainty

**Why this matters (the failure modes it catches):**
- Yesterday's Dodgers PRIME +6 zero-delta loss = single-model overconfidence. Ensemble would have flagged: pitcher + offense agree, but recency + matchup + bullpen don't. 2-vs-3 split → uncertain, no PRIME.
- Pirates +5 confluence loss same pattern: pitching agreed, bullpen + recency disagreed.

**Phased build (post-launch, ~15-20 hours total):**
- Week 5-6: Build Pitcher Quality + Offense Strength + Recency models in parallel. Run informational alongside v3 Ridge.
- Week 7: Add Matchup + Bullpen models. Consensus voting layer.
- Week 8-9: Backtest ensemble vs v3 on resolved games. If MAE drops 0.3+, promote ensemble to primary. If not, ditch.
- Week 10+: Live ensemble runs primary, v3 stays as one of the 5 votes for redundancy.

**Why NOT to build now (pre-launch):**
- Need ~3 weeks of L10 + bucket + recency data captured first
- Each model needs individual backtest before ensemble can be validated
- Pre-launch focus should be shipping + validating what's already built
- Adds complexity that's premature without baseline performance data

**How to apply:** When Andy revisits model architecture post-launch (May/June), default to this phased ensemble plan. If pre-launch, defer — the data we're collecting now (BB%, HR/9, inning_1, L10) is exactly what feeds the v2 models.
