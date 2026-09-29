---
name: audit-721-full
description: "Full 7/21 audit: model layer breakdown, external cross-pattern, postponement impact, bug: doubleheader primary_play null."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-22T19:35:21.311Z
---

**Set 2026-07-22 morning after full 7/21 audit.**

## Model layer performance 7/21 (ML direction, graded)

| Layer | W-L | Pct | Sample |
|---|---|---|---|
| **Model (v4 XGBoost)** | 3-9 | **25%** ⚠️ | 12 |
| **Jerry (LLM)** | 8-5 | 61% | 13 |
| **Panel (SP+BP)** | 8-3 | **72%** ⭐ | 11 |
| **Confluence** | 6-4 | 60% | 10 |
| External consensus | 7-6 | 53% | 13 |

Single-night sample, so don't fade v4 on this alone. But watch — 60d audit
had v4 at 58.4% on sides; a recency drift below that band would call for
reweight.

**Why:** Panel keeps outperforming quietly. On 7/21 alone panel was the best
side layer despite being DROPPED from totals in 7/18 audit. The panel
side-weight (0.4 when non-null) is already correct per 7/21 reweight.

**How to apply:** Continue weighted composite_spread as shipped 7/21.
Flag a recency-panel-side audit for weekend (n=7 last week vs 60d).

## Card grade 7/21 (verified)

- **POTD BOS ML** — NO ACTION (BAL@BOS postponed)
- **DAWG LAD PRIME 81** — LOSS (LAD lost 2-1 in pitcher duel)
- **BUCKET NRFI Twins@Guardians 90 PRIME** — LOSS (YRFI hit)
- **BUCKET YRFI CWS@TEX** — WIN (0-10 blowout, 1st-inning runs)
- **STRONG YRFI BAL@BOS** — NO ACTION (postponed)
- **SKIP prop Will Warren PIT@NYY** — NO ACTION (postponed)
- **PROP Curtis Mead Hits Over WSH@COL**: TBD (no mlb_props table entry surfaced)
- **PROP Zack Wheeler Ks Over LAD@PHI**: TBD (LAD 2-1 pitcher duel)
- **PROP David Peterson ER Over**: TBD

**Verified record: 1W-2L on graded plays.** Not brutal; postponements
hollowed the card.

## Postponement pattern (structural risk)

2 games (BAL@BOS, PIT@NYY) rained out same night = **13% of slate wiped**.
Both were card headliners (POTD + STRONG YRFI). Card design should
factor weather/rain risk when selecting POTD — a single-game POTD in a
rain-risk market is fragile.

**How to apply:** Add weather-risk gate to POTD selection. If forecast
rain probability ≥ 40% at kickoff, POTD deprioritized or paired with a
backup dawg pick.

## External x internal cross-pattern findings

- **On SDP@ATL**: EVERY layer (model+jerry+panel+confluence+externals+
  2 boost tags) leaned HOME (ATL). Padres won 8-3. **Universal-agreement
  loss = no signal to catch it.** These will always happen; not a fix
  target.

- **On WSH@COL**: model+jerry+externals AWAY (WSH), panel+confluence HOME
  (COL). Rockies won 8-7. **Panel + confluence right on split-decision
  game.** Reinforces panel-side signal quality.

- **On LAD@PHI (DAWG)**: All 4 layers leaned AWAY (LAD). LAD lost 2-1 by
  1 run in pitcher duel. Model directionally right, wrong on outcome.
  Small-sample variance loss, not a systemic issue.

**Aggregate external consensus ~coinflip (53% n=13)** — externals as a
whole are NOISE. Individual audit-flagged sources (5-star fade, boost)
have edge; aggregate consensus does not.

## BUG SURFACED: doubleheader primary_play null

PIT@NYY doubleheader today (7/22):
- Game 1: sweat 86 PRIME, `primary_play_computed_at` set but `primary_play` = None
- Game 2: sweat 46 PASS, same

The scorer runs (timestamp set) but returns null on both. Likely the
doubleheader logic handling. **Priority: reproduce locally + fix before
tonight's card. If not fixable in time, skip PIT@NYY entirely tonight.**

## Related

- [[project_composite_debias_finding_712]] — flagged v4 broken 7/12
- [[project_model_reweight_721]] — 7/21 reweight (v4 removed from totals, kept on sides)
- [[project_30d_lens_audit_718]] — panel dropped from totals
- [[feedback_dont_fade_prime_on_pattern_alone]] — don't reweight off one bad night
- [[feedback_confidence_in_first_pass]] — honest confidence bounds
- [[project_tiktok_public_streak_721]] — user rough-night context
