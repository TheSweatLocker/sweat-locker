# Sweat Locker Signal Playbook

**Last updated:** 2026-08-15
**Purpose:** Single source of truth for every signal we track, its historical hit rate + sample size, its tier (validated/discovery/unvalidated/anti-validated), and how it should be weighted in decisions.

This playbook is what Jerry's synthesis prompt and the primary_play resolver should ultimately reference. Any signal not in this document should NOT influence a pick.

---

## Legend

| Tier | Meaning | Vote Weight |
|---|---|---|
| ✅ **VALIDATED** | n ≥ 50, hit rate ≥ baseline + 5pp | Full weight (1.0) |
| 🔬 **DISCOVERY** | n = 15-49, positive edge | Half weight (0.5) |
| ⚪ **UNVALIDATED** | No backtest yet | Info-only, no vote |
| ❌ **ANTI-VALIDATED** | Backtest shows below baseline | Invert or drop |

Baseline: ML/RL/Total picks at -110 → breakeven ≈ 52.4%.

---

## PART 1 — Signal Inventory

### 1A · Model Outputs

| Signal | Description | Hit Rate | n | Tier | Weight | Notes |
|---|---|---|---|---|---|---|
| MC ML high-conf (≥60%) | Monte Carlo sim ML pick | **46.6%** lifetime | 1121+ | ❌ ANTI-VALIDATED alone | 0 (invert to fade at extreme?) | MC alone is BELOW breakeven; only trust when combined with other validated signals |
| MC Total ≥60% OVER / ≤40% UNDER | MC total pick | **51.4%** lifetime | 1042+ | ⚪ ~coinflip | 0.3 (soft) | Marginal edge; treat as tie-breaker |
| MC NRFI/YRFI | 1st-inning pick | ~52-55% (segmented) | Varies | 🔬 DISCOVERY | 0.5 | See NRFI cohort stats separately |
| v4 XGBoost projected_total | Model-vs-close delta | 51.4% lifetime | 1042 | ⚪ marginal | 0.5 | Slightly better than MC on totals |
| v5 XGBoost | Newer runs model | untested | - | ⚪ | 0 | Live monitoring |
| Jerry synth pred_total | LLM total projection | Historically noisy | - | ⚪ | 0.3 | Don't use as primary basis |
| Panel implied total | Multi-lens implied | No dedicated backtest | - | 🔬 | 0.5 | Divergence from close is signal |
| **Lens consensus (5-6 of 6)** | Multiple methods agree | Cohort-level tracked | - | ✅ | 1.0 | Consensus IS the signal |

### 1B · Pitcher Metrics

| Signal | Description | Hit Rate | n | Tier | Weight | Notes |
|---|---|---|---|---|---|---|
| SIERA gap (fav-arm ML) | Better SIERA = winner? | **37-48%** across gap sizes | 172 | ❌ ANTI-VALIDATED | 0 | Backtested 2026-08-15 · FAILS as directional signal |
| SIERA ACE DUEL (both ≤ 3.00) | Both starters elite → UNDER | **64.7%** | 17 | 🔬 DISCOVERY | 0.7 | Small sample but promising |
| xERA gap (fav-arm ML) | Better xERA = winner? | 41-48% across gap sizes | ~200 | ❌ noise | 0 | Similar to SIERA — not directional |
| xERA gap → UNDER (2.0+R gap) | Big pitcher mismatch = one team scoreless | Not formally backtested | - | 🔬 | 0.5 | Engine convention |
| xBA_allowed (Statcast) | Better than raw BAA for hits props | Wired 8/15 | - | ⚪ | 0 (new) | Now feeds `_blended_projected_hits` |
| Framing K bonus | Elite framer → +Ks for starter | Wired 8/15 | - | ⚪ | 0 (new) | Now feeds K prop scorer |
| TTTO penalty | 3rd time through order = worse | Formula-based | - | ⚪ | 0 | No backtest |
| Days rest | Fatigue vs long-rest | Long-rest home ATS 57% | ~200 | 🔬 | 0.5 | Per project memory |
| L3 ERA (recent form) | Streaky pitcher signal | - | - | ⚪ | 0.5 | Used as prop scorer input |
| Pitcher vs THIS team historical | Mastery/anti-mastery | PRIME-agree 7-0 / disagree 3-4 | 14 | 🔬 | 0.7 | Small but meaningful per memory |

### 1C · Team Offense

| Signal | Description | Hit Rate | n | Tier | Weight | Notes |
|---|---|---|---|---|---|---|
| **OPS L14 dual ICE (both ≤ .65)** | Cold offenses → UNDER | **64.7%** | 34 | 🔬 DISCOVERY strong | 1.0 | Best OPS L14 cut |
| **OPS L14 dual COLD (both ≤ .70)** | Moderate cold → UNDER | **56.4%** | 165 | ✅ VALIDATED | 1.0 | Largest validated cohort |
| **OPS L14 dual HOT (both ≥ .78)** | Regression fade → UNDER | **60.3%** | 68 | ✅ VALIDATED | 1.0 | Contrarian signal |
| OPS vs opp hand ≥ 0.75 avg | Both lineups strong vs opp hand → OVER | 55.0% | 84 | 🔬 | 0.5 | Marginal |
| Handedness wRC+ Δ ≥ 15 + edge ≥ 110 | Asymmetric hand advantage | 63.6% | 22 | 🔬 SMALL | 0.5 | Too small to trust hard |
| Team season wRC+ | Baseline talent | Baseline for other cohorts | - | ⚪ | 0 | Input to other cohorts |
| L10 run diff (momentum) | Δ ≥ 2 + leader ≥ +1 | No formal backtest | - | ⚪ | 0 | Discussed 8/15 but not shipped |
| L7 OPS vs bad SP | Hot bats vs bad starter | Sample TBD | - | 🔬 | 0.5 | Exists in play_of_day |
| Inning bucket wRC+ (7-9) × BP fatigue | Late-inning damage | Untested | - | ⚪ | 0 | New cohort 8/15 |

### 1D · BABIP Regression Flag

| Signal | Description | Hit Rate | n | Tier | Weight | Notes |
|---|---|---|---|---|---|---|
| BABIP hot flag | L14 R/G ≥ season+1 | **94% regressed DOWN** | 143 | ✅ (as adjuster) | Adjust -1.89R | NOT a direct pick — R/G adjuster |
| BABIP cold flag | L14 R/G ≤ season-1 | **96% regressed UP** | 140 | ✅ (as adjuster) | Adjust +1.65R | NOT a direct pick — R/G adjuster |

**Important:** BABIP flag ≠ OVER/UNDER pick. It shifts projected total by ~1.5R. Use as input to model projection, not standalone pick.

### 1E · Bullpen

| Signal | Description | Hit Rate | n | Tier | Weight | Notes |
|---|---|---|---|---|---|---|
| Bullpen effective ERA (rest-adjusted) | Shipped 8/15 | New | - | ⚪ | 0 | Replaces season BP ERA in some contexts |
| Bullpen availability (0-100) | Relievers rested | New | - | ⚪ | 0 | Feeds effective ERA calc |
| BP inning splits (7-9 K%/HR9) | Late-inning quality | Present in scout only | - | ⚪ | 0 | Not driving decisions yet |
| Both BPs shaky → OVER | Both BP effective ERA ≥ 4.50 | Not backtested | - | ⚪ | 0 | Play_of_day driver exists |

### 1F · Public Splits (BOTH sources)

| Signal | Description | Hit Rate | n | Tier | Weight | Notes |
|---|---|---|---|---|---|---|
| OC money% ≥ 60 + bets% < 55 (BACK) | Sharp divergence, follow | 60.7% | 28 | 🔬 | 0.7 | Original seed pattern |
| **OC money% ≥ 80 ML (FADE)** | Loud sharp on ML is fade signal | **63.9%** | 36 | 🔬 | -1.0 (invert to fade) | Backtested 8/15 - CONTRADICTS follow assumption |
| **Div ≥ 20 (money-bets) (FADE)** | Big divergence → fade | **62.1%** | 29 | 🔬 | -1.0 (invert) | Contradicts sharp-follow pattern |
| RL market money% ≥ 70 (BACK) | Sharps ARE right on RL | 60-64% | 14-15 | 🔬 | +0.7 (follow) | Market-specific |
| FR handle% > bets% by 15+ | FR sharp divergence | No standalone backtest | - | ⚪ | 0 | Needs its own backtest |
| **Cross-source SHARP_CONFIRMED** | Both OC + FR agree on sharp side | Not backtested yet | - | 🔬 | 1.0 (aspirational) | Highest signal weight when it fires — pending backtest confirmation |
| Cross-source SPLIT | OC and FR disagree | Signal unreliable | - | ⚪ | 0 | Do not use |

### 1G · Line Movement Flags

| Signal | Description | Hit Rate | n | Tier | Weight | Notes |
|---|---|---|---|---|---|---|
| STEAM (multi-book coordinated) | Fast synchronized move | Not standalone backtest | - | ⚪ | 0.5 | Historically respected |
| RLM (line moves against public) | Reverse line movement | Not backtested | - | ⚪ | 0.5 | Classic sharp indicator |
| LIMIT (whale money) | Whales moved a book | Small n | - | ⚪ | 0.3 | |
| SHARP_CONFIRMED (cross-source) | Both OC + FR agree | Aspirational | - | 🔬 | 1.0 | Wired 8/15, needs data |
| SHARP_LEAN (one source) | Muted single-source | Weaker | - | ⚪ | 0.3 | Show but don't lean |

### 1H · Sharp Scenario Matches (per-game historical)

Table `sharp_scenario_game_matches` per-game lookups with specific hit rates. Best cells today (n≥20):
| Scenario | Hit Rate | n |
|---|---|---|
| ml HOME BACK LEAN_BACK | 75.0% | 16 |
| ml HOME BACK BACK | 67.7% | 31 |
| ml HOME BACK BACK | 60.0% | 30 |
| total UNDER BACK LEAN_BACK | 59.3% | 28 |
| total UNDER BACK LEAN_BACK | 58.1% | 34 |

Weight: **1.0** when hit rate ≥ 60% AND n ≥ 20.

### 1I · Pattern Engine Registry

Auto-refreshed nightly. Current MLB top:
| Pattern | Hit Rate | n | Tier | Dir |
|---|---|---|---|---|
| oc_only_sharp_signal_60+ | 62.5% | 16 | 🔬 | FOLLOW |
| auto_pair_oc_money>=65_bets<55 | 61.1% | 18 | 🔬 | FOLLOW |
| auto_pair_oc_money>=60_bets<55 | 60.7% | 28 | 🔬 | FOLLOW |
| auto_pair_oc_divergence>=20 | 55.6% | 18 | 🔬 | FOLLOW |
| **oc_ml_extreme_money_gte_80** | 50.0% | 36 | 🔬 | **FADE** (seeded 8/15) |
| **oc_divergence_gte_20** | 50.0% | 22 | 🔬 | **FADE** (seeded 8/15) |

Note contradiction: same OC divergence pattern shows 55-61% BACK in DISCOVERED cells AND 50% FADE in seeded FADE cells. Both directions being tracked; whichever accumulates higher validated sample wins.

### 1J · Refit Conviction (props only)

| Signal | Description | Hit Rate | n | Tier | Weight |
|---|---|---|---|---|---|
| refit 95-100 | Max confidence | 55.2% | 67 | 🔬 | 1.0 (not "guaranteed") |
| refit 75-84 | **BEST band** | 62.7% | 59 | ✅ | 1.5 (premium) |
| refit 65-74 | Moderate | 49.4% (worst!) | 85 | ❌ | 0 |
| refit 55-64 | Low-mid | 58.8% | 34 | 🔬 | 1.0 |
| refit 45-54 | Coinflip zone | 52.4% | 42 | ⚪ | 0 |
| refit 35-44 | (fade zone?) | 62.3% | 53 | 🔬 | +1.0 to FADE direction |
| refit < 25 | Fade | 47.1% | 155 | 🔬 | 0.5 to opposite |

**NOT monotonic** — 75-84 beats 95-100. Calibration is noisy.

### 1K · Rule Fire Stats (Jerry pre-publish overrides)

| Rule | Hit Rate | n |
|---|---|---|
| refit_override REFIT_BAND_UNPROVEN | **62.1%** | 29 |
| pipeline_repair PITCHER | 46.7% | 15 |

### 1L · External Picks (handicappers)

Table: `external_picks` — schema: `source`, `pick_side`, `pick_line`, `odds_american`, `result`, `confidence`, `fade_flag`, `resolved_at`.

**Currently tracked handicappers:** *(TODO: query per-source hit rate rollup)*

**How to weight:**
- External source with 60%+ lifetime + n ≥ 50 → agree = confirming vote (weight 0.5)
- External source below 50% → their pick is a FADE signal (weight 0.5 opposite)
- Multiple externals agreeing on same side → weight scales with agreement count

**Gap:** No `external_pick_grades` rollup table. **NEEDS BUILT:** nightly script that computes per-source hit rate over rolling 30d/90d/lifetime, stored for lookup.

### 1M · Situational

| Signal | Description | Hit Rate | n | Tier | Weight |
|---|---|---|---|---|---|
| Park factor (Coors etc) | Extreme parks | Historically respected | - | 🔬 | 0.5 |
| Weather (wind, temp ≤45°F) | NRFI temp ≤45°F: 79.2% | ~60 | 🔬 | 0.7 |
| Umpire tendencies (over_rate) | Suppress/confirm total picks | Cohort in play_of_day | - | 🔬 | 0.5 |
| Days rest | Long-rest home ATS 57% | ~200 | 🔬 | 0.5 |

---

## PART 2 — Weighting Framework

### Rule: Only VALIDATED signals cast primary votes

If a game has ZERO validated signals firing → **NO PICK** (do not fabricate a fallback LEAN).

Currently the engine fabricates fallback picks. Real fix needed.

### Rule: Anti-validated signals get inverted or dropped

MC alone at 46.6% ML lifetime → cannot be the primary basis for a PRIME pick.
OC ML money%≥80 → 63.9% FADE → invert direction.

### Rule: Signal count matters more than any single loud signal

PRIME threshold should require: **3+ validated signals** aligned on same side, no anti-validated signal contradicting.

---

## PART 3 — Per-Market Decision Rules

### Moneyline (ML)
1. Only pick when 3+ validated signals align on same side
2. If OC ML money% ≥ 80 with no cross-source FR agreement → **fade** the sharp side
3. If cross-source (OC + FR) both agree on sharp side + at least one other validated signal → PRIME-eligible
4. Never make MC alone the primary basis

### Runline (RL)
1. Sharps ARE right on RL per backtest (60-64% at money%≥70)
2. Heavy fav (-200+) covers only 29% historically → juice trap
3. Spread delta 1.0-1.5 = 40-43% trap zone

### Total (OVER / UNDER)
1. Dual OPS L14 cold (≤ 0.70) → UNDER (56.4% n=165, VALIDATED)
2. Dual OPS L14 hot (≥ 0.78) → UNDER regression fade (60.3% n=68)
3. Ace duel SIERA ≤ 3.00 both → UNDER (64.7% n=17)
4. BABIP flag adjusts projected total ±1.5R, then compare vs close
5. Total spread models (v4, panel) → look for consensus with cohorts

### Props
1. **Refit band 75-84 = PREMIUM** (62.7% n=59)
2. Refit 100 = 57% (not "guaranteed") — treat as STRONG not PRIME
3. Refit 35-44 = FADE zone (62.3% hits the other side)
4. K props: apply framing_k_bonus (elite framer +0.8 K)
5. Hits props: use xBA_allowed > baa_allowed cascade
6. Batter Hits O 0.5 at -200+ = juice trap (PRIME 69% but juice bleeds edge)

---

## PART 4 — Trap List (do NOT pick)

| Trap | Rule | Hit / n |
|---|---|---|
| Heavy-fav ML at -200+ | Juice trap on covers | -1.5 cover 29% |
| Batter Hits O 0.5 at -200+ | Juice bleeds edge | 69% PRIME |
| Middle-zone spread delta 1.0-1.5 | Dead zone | 40-43% |
| Refit=100 without engine agreement | Not "guaranteed" | 57.1% n=63 |
| MC ≥60% alone (no other confluence) | Below breakeven baseline | 46.6% lifetime |
| Sharp $ ML money%≥80 without cross-source | Fade signal not follow | 64% FADE |
| Same-day generic pitcher ref in Jerry prose | Auto-scrub via Layer D | - |
| Fabricated stats in Jerry | Auto-strip | - |
| POTD max juice -200 | Auto-skip | - |
| N/YRFI on cards without extreme + STRONG+ | Coinflip variance | - |
| Full-slate suppression on any bucket <45% | Fade the OTHER side instead | - |

---

## PART 5 — Jerry Voice Constraints

### DO
- Cite ONLY validated signals as reasoning
- Use actual pitcher names, never "opposing starter" or "the [team] starter"
- Match verdict to narrative direction (never say FADE + write BACK reasoning)
- Reference historical hit rate + n for any pattern cited
- Cite refit conviction when it disagrees with tier calibration
- Note when cross-source public splits AGREE (highest signal weight)

### DO NOT
- Cite MC probability as sole basis for a pick
- Cite SIERA gap as directional evidence (backtest: 37-48% below coinflip)
- Reuse the same closing phrase across multiple reads
- Cite data-provider or handicapper names in prose (except External Picks panel)
- Say "KenPom" — use "efficiency model" (per NCAAB rules)
- Say "Sweat Less" — brand tagline is "More Data, Less Sweat"

### Refit override rules
- Refit ≥ 80 with FADE verdict → force-flip to BACK (with LEAN cap)
- Refit 65-79 with FADE verdict → warn but don't flip
- Refit ≤ 20 with BACK verdict → force-flip to FADE

---

## PART 6 — External Picks Integration

### Current state
- `external_picks` table has structure for tracking picks + grades
- Sources tracked include (per resolver_note field examples): ATS, RotoBaller, etc.
- Grades resolve via `resolver_note` field

### What we need to build
1. **`external_source_records`** nightly rollup table:
   - Per source × sport × market: rolling 30d/90d/lifetime hit rate, ROI, sample size
   - Update via cron script
2. **External integration in play_of_day**:
   - Read active external picks for game
   - If external source hit rate ≥ 60% (lifetime n≥50) → agreement = confirming vote (weight 0.5)
   - If external source hit rate ≤ 47% → their pick = FADE signal (weight 0.5 opposite)
3. **External confluence display** in Sweat Card
   - Show top-graded external agreeing with our pick as confidence indicator
   - Show high-graded external DISAGREEING as warning

---

## PART 7 — Decision Sequence (Jerry's Executable Playbook)

For each game, in order:

```
STEP 1: HARD BLOCKS (auto-fail — do not publish)
  - Fabricated stat in prose (Layer D scrub)
  - Verdict/narrative direction mismatch
  - Generic pitcher reference
  - Force-block trap conditions (see Part 4)

STEP 2: SIGNAL COLLECTION
  For each signal in Part 1:
    - Check if it fires on this game
    - Get its historical hit rate + n
    - Determine tier (VALIDATED / DISCOVERY / UNVALIDATED / ANTI)

STEP 3: VOTE AGGREGATION
  - VALIDATED signals → full weight vote to their direction
  - DISCOVERY signals → half weight vote
  - ANTI-VALIDATED → invert direction OR drop
  - UNVALIDATED → log for observability, no vote

STEP 4: TIER GATE
  - PRIME: 3+ validated signals aligned, no anti-validated contradiction, net weight ≥ 3.0
  - STRONG: 2+ validated aligned, net weight ≥ 2.0
  - LEAN: 1 validated + 1 discovery aligned, net weight ≥ 1.5
  - PASS: no aligned validated signals

STEP 5: TRAP CHECK (post-tier)
  - If pick is in any Part 4 trap zone → downgrade tier or PASS
  - If refit conviction contradicts tier by ≥ 30pt → apply refit override
  - If cross-source public splits DISAGREE on this pick → cap at LEAN

STEP 6: EXTERNAL CONFLUENCE
  - Query external_picks for this game
  - Weight by source's rolling hit rate
  - Log agreement/disagreement to sweat_breakdown

STEP 7: WRITE READ
  - Cite ONLY validated signals in reasoning
  - Include hit rate + n for any pattern referenced
  - Match verdict direction to narrative
  - Never MC-anchor or SIERA-anchor

STEP 8: POST-PUBLISH AUDIT
  - jerry_pre_publish_audit checks (Layer D, scenario matrix, refit sync)
  - Auto-repair known classes
  - Block if criticals remain
```

---

## PART 8 — What's Missing (Gaps to Build)

Prioritized by ROI:

| # | Gap | Priority | Effort | Impact |
|---|---|---|---|---|
| 1 | **Signal Registry** unified table | HIGH | 2h | Auditable per-pick reasoning |
| 2 | **External source records** rollup | HIGH | 2h | Real handicapper weighting |
| 3 | **Cross-source SHARP_CONFIRMED backtest** | HIGH | Wait 30-60d for data | Validate our cross-source gate |
| 4 | **Fade-sharp patterns validation** | HIGH | Wait 30d for pattern miner | Confirm invert-sharp finding |
| 5 | **Remove SIERA gap side driver** | MED | 30min | Fix anti-signal in play_of_day |
| 6 | **BABIP as R/G adjuster, not pick** | MED | 1h | Fix BABIP misuse in engine |
| 7 | **L10 momentum backtest** | MED | 2h | Currently untested driver in play_of_day |
| 8 | **Pitcher pattern registry** (SIERA-specific) | MED | 2h | Framing K bonus, xBA validate |
| 9 | **Umpire tendencies backtest formalized** | LOW | 2h | Ump cohort in play_of_day |
| 10 | **Weather/park backtest** | LOW | 2h | NRFI temp ≤45°F was 79%; validate others |

---

## Change Log

- **2026-08-15 (initial):** Playbook created after slate audit revealed engine was over-weighting MC/SIERA/sharp signals with no historical basis. Fade-sharp analysis confirmed OC money%≥80 on ML is a fade signal not follow. SIERA gap backtested as anti-predictive for ML (37-48% fav-arm win). Refit calibration curve non-monotonic (75-84 band beats 95-100).
