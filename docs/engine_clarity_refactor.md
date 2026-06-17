# Engine Clarity Refactor — Design

**Status:** Spec / 2026-06-17
**Author:** ag + claude
**Priority:** Post-launch P0 (architectural cleanup that unblocks reliable autonomous operation)
**Related:** [project_unified_taxonomy_decision](memory), [generate_sweat_card.py](../mlb_pipeline/generate_sweat_card.py), [play_of_day.py](../mlb_pipeline/play_of_day.py), [cohort_signals.py](../mlb_pipeline/cohort_signals.py), [generate_mlb_game_reads.py](../mlb_pipeline/generate_mlb_game_reads.py)

---

## 1. Problem

The system has accreted **four parallel "PRIME" labeling systems**, **three independent "sweat score" sources**, **overloaded field names** across publishing surfaces (POTD / LOCK / DAWG / top_8 / props), and **a class of Jerry-narrative numbers that are hardcoded instead of live**. The result:

1. **Daily intervention is required** to override mispicked POTDs because the multi-system tier disagreements produce confusing surfaces (PRIME with score 62, total signals labeled as side conviction, NRFI score displayed as headline sweat).
2. **Developer (and AI assistant) confusion** when reasoning about picks. Field names like `score`, `tier`, `confidence` mean different things in different objects.
3. **Stale number leakage to Jerry narratives** — buy-down cohort hit rates were hardcoded from a 6-week backtest and never refreshed; LLM cites them as if live.
4. **No cross-dimensional narrative validation** — side picks can be justified with total signals (the "both pitchers elite → HOU ML" class of confusion).
5. **POTD construction from non-card metadata** — the POTD selector can theoretically inherit a `score: 91` from an NRFI lock and surface it as if it were a moneyline conviction.

**Cost in practice:** Every day, the published card requires manual review and sometimes manual swaps. The "system works without intervention" goal is not met.

---

## 2. Inventory — what exists today

### 2.1 The four PRIME systems

| Source file | PRIME trigger | Scope |
|-------------|---------------|-------|
| `play_of_day.py:_dim_tier()` | sweat sub-score ≥ 80 | sweat dimensions |
| `generate_props.py:tier_for()` | conviction ≥ {70, 82, 85} depending on prop_type | per-prop |
| `game_context.py:3945`, `generate_daily_degen.py:466` | `confluence_net ≥ 4` (a count of independent signals, NOT a score) | side picks |
| `generate_sweat_card.py:957` | `confidence == 'elite'` (a data-availability flag, NOT a confidence) | POTD label |

These can disagree on the same pick. A prop showing "PRIME 62" can be a prop_pipeline STRONG (conviction 62 = below PRIME 70 for bb/ha props) **labeled PRIME** via confluence_net ≥ 4 on the game it's in.

### 2.2 The three independent "sweat" scores

| Score | Range | Meaning | Threshold for PRIME |
|-------|-------|---------|---------------------|
| `dimensions.side.score` | 0-100 | side-bet signal sum | ≥80 |
| `dimensions.total.score` | 0-100 | total-bet signal sum | ≥80 |
| `dimensions.prop.score` | 0-100 | prop-bet signal sum | ≥80 |
| **headline sweat** | 0-100 | max of above three | inherited |
| `prop_pipeline.conviction` | 0-100 | per-prop math output | varies by prop_type |
| `NRFI score` | 0-100 | 1st-inning signal | banded 70/80/90/94/95+ |
| `POTD score.total` | 0-100 | sweat_score.total snapshot at POTD time | inherited |
| `DAWG conviction` | 0-100 | DAWG-specific sub-score | DAWG-specific bands |

All called "score" or "conviction" depending on context. **Range and shape don't distinguish them.**

### 2.3 Field-name collisions across publishing objects

| Object | `score` means | `tier` means | `confidence` means |
|--------|---------------|--------------|---------------------|
| `potd` | sweat_score.total snapshot | — | data-availability flag (NOT model confidence) |
| `lock` | NRFI score (1st-inning signal) | NRFI cohort tier (PRIME = 90-94 band) | n/a |
| `secondary_lock` | YRFI score | YRFI cohort tier | n/a |
| `dawg` | n/a | DAWG cohort tier | n/a (uses `conviction`) |
| `top_8[i]` | varies | varies by play type | n/a |
| `top_props[i]` | n/a | prop_pipeline tier (per-type threshold) | n/a |
| `dimensions.*` | dimension sub-score | dimension tier (80/65/50 ladder) | n/a |

**Same field names, different meanings.** A reader (human or LLM) parsing the sweat card cannot tell which `score` is which without knowing which object it came from.

### 2.4 Numbers Jerry recites — sources

| Number Jerry cites | Source | Live? |
|--------------------|--------|-------|
| "STRONG_EDGE_UNDER signal (70%, 28-11)" | `cohort_signals.summarize_for_struct()` line 197-205, reads live `jerry_cache.cohort_signals` row | ✅ live, refreshed nightly |
| "Cohort hit rate 80%" (buy-down cheat-line) | `generate_mlb_game_reads.py:720-728` — **was hardcoded** | ❌ stripped 2026-06-17 (commit pending), replaced with `None` |
| "67.7% historical" (DAWG cohorts) | DAWG generator pulls from cohort_signals | ✅ live |
| "NRFI 90-94 audits 50%" | hardcoded annotation in `play_of_day.py:776` | ⚠️ stale annotation, used internally only — NOT fed to Jerry struct |
| Free-form percentages invented by Claude in narrative | LLM output — **no validator** | ❌ unverified, hallucination possible |

---

## 3. Goals

1. **Single source of truth per pick type.** PRIME for sides means one thing, derived from one calculation. Same for totals and props.
2. **No field-name collisions.** Every numeric field is namespaced so its meaning is unambiguous: `nrfi_score`, `dim_score_side`, `prop_conviction`, etc.
3. **All Jerry-cited numbers must be in the struct.** Post-LLM validator confirms every percentage and W-L count in narrative appears in the structured prompt input.
4. **Cross-dimensional narrative validation.** Side narratives can only cite side_drivers, totals only total_drivers, props only prop_drivers. No more "both SPs elite → HOU ML" leakage.
5. **POTD construction is dimension-pure.** POTD pulls from `dimensions.{side,total,prop}.play` — never invents picks from LOCK/DAWG metadata.

Out of scope (v1 of this refactor):
- Sweat dimension threshold recalibration (separate audit workstream)
- Adding new model inputs
- Changing cohort engine math

---

## 4. The six-phase fix

### Phase 1 — Field name namespacing
**File:** `play_of_day.py`, `generate_sweat_card.py`, `cohort_signals.py`, all callers of those objects (incl. app).

**Changes:**
- `lock.score` → `lock.nrfi_score` (rename, explicit)
- `secondary_lock.score` → `secondary_lock.yrfi_score`
- `potd.score.total` → `potd.sweat_score_total`
- `dimensions.{side,total,prop}.score` → keep (already clear with the dimension parent)
- `dawg.conviction` → keep but add `dawg.tier_source: 'dawg_cohort'`
- `top_8[i].tier_source: <source>` added to every pick

**Migration:** server emits BOTH old and new fields for one week (back-compat), app updates render paths to use new names, then old fields dropped. Schema migration on `daily_best_bet_history.confidence` to a new `confidence_source` enum.

### Phase 2 — Single PRIME definition per pick type
**File:** `play_of_day.py`, `generate_props.py`, `generate_sweat_card.py`, `game_context.py`, `generate_daily_degen.py`.

**Rules (replaces multi-system PRIME):**
```
SIDE PRIME    = dimensions.side.score  ≥ 80 AND resolver_side  ∈ {STRONG, ELITE}
TOTAL PRIME   = dimensions.total.score ≥ 80 AND resolver_total ∈ {STRONG, ELITE} AND projection_gap_pass
PROP PRIME    = prop_pipeline.conviction ≥ TIER_THRESHOLD[prop_type] AND live_tier_x_type_edge ∈ {EDGE, CALIBRATED}
NRFI/YRFI     = NOT eligible for PRIME label publicly (already demoted from POTD per 5/30 — this codifies)
```

**Deleted (with grep-replace migration):**
- `if confluence_net >= 4: tier = 'PRIME'` (game_context.py:3945, generate_daily_degen.py:466)
- `potd_tier = "VALUE" if confidence == "value" else "PRIME"` (generate_sweat_card.py:957) — replaced with sweat-score-based tier
- Hardcoded prop_type tier ladders in `generate_props.tier_for()` — replaced with live tier×type lookup (we already track this in `track_live_tier_record.py`)

### Phase 3 — POTD construction is dimension-pure
**File:** `play_of_day.py` POTD selector block (line ~2700+).

POTD candidate generation must ONLY pull from:
- `dimensions.side.play` for side POTDs
- `dimensions.total.play` for total POTDs
- `dimensions.prop.play` for prop POTDs

Forbidden: constructing a POTD from `lock` (NRFI), `secondary_lock` (YRFI), `dawg`, or any object that's not a sweat dimension play. The dimension's `play` field is the contract.

Today's incident: the LOCK with NRFI score 91 was misread by Claude as "HOU ML PRIME 91" → manual swap chaos. This phase makes that misreading impossible because POTD candidates wouldn't come from LOCK.

### Phase 4 — Number-attribution validator (Jerry)
**File:** new `validate_jerry_narrative_numbers.py`, hooked into `generate_mlb_game_reads.py:call_claude()`.

After Claude returns narrative, scan for:
- Percentages (`r'\d{1,3}(?:\.\d+)?\s*%'`)
- W-L counts (`r'\d+-\d+(?: lifetime)?'`)
- Hit rates as decimals or fractions

For each, verify the value appears in the struct (within 1-2pp tolerance). If a number can't be traced to a struct value:
- Retry once with explicit "do not invent percentages" instruction
- On second failure, strip the unverifiable number from narrative (regex replace)
- Log the hallucination to `jerry_validation_log`

Mirror the player-attribution validator pattern (project_jerry_attribution_validator.md).

### Phase 5 — Cross-dimensional narrative validator
**File:** same validator hook.

Classify the narrative's pick type (side vs total vs prop). Then verify:
- Side narrative only cites side_drivers and side-eligible signals (confluence, model spread, ML cohort)
- Total narrative only cites total_drivers (xERA, NRFI, park, weather)
- Prop narrative only cites prop_drivers (per-player stats)

Detection: regex for side-signal keywords ("both pitchers elite", "NRFI", "park factor") in narratives classified as side picks → flag + retry.

### Phase 6 — Buy-down hit rate dynamic computation
**File:** `generate_mlb_game_reads.py:_resolver_block()` area + new `buy_down_calibration.py`.

The hardcoded buy-down hit rates were stripped 2026-06-17 (set to `None`). To restore the cohort hit rate claims, build a dynamic computation:

1. New script `buy_down_calibration.py` runs nightly after `resolve_game_results.py`
2. Computes hit rate for each buy-down cohort (`all_three_agree`, `model_edge_2`, `consensus_loud`) over rolling 30d / 60d / 90d windows
3. Writes to new table `buy_down_calibration` keyed by `(cohort, window, direction)`
4. `generate_mlb_game_reads.py` reads from this table instead of hardcoded values

Cell calibration must have `n >= 30` to be quoted to Jerry — below threshold, omit the percentage entirely (current None behavior).

---

## 5. Migration order

1. **Phase 4 + 5 (narrative validators)** — ship first. Lowest risk, highest immediate value. Stops Jerry from inventing numbers and cross-citing dimensions.
2. **Phase 6 (buy-down dynamic calibration)** — ships independently; restores legitimate cohort claims on buy-down surface.
3. **Phase 2 (single PRIME definition)** — coordinate with the existing universal taxonomy refactor (PRIME/STRONG/LEAN). One atomic PR.
4. **Phase 3 (POTD dimension-pure)** — same PR as Phase 2.
5. **Phase 1 (field namespacing)** — last because it touches the most files. Two-week back-compat window. Schema migration end of window.

---

## 6. What gets deleted

By end of this refactor:

- `confluence_net >= 4 → PRIME` shortcut (2 sites)
- `confidence == 'elite' → PRIME` POTD shortcut (1 site)
- Hardcoded prop_type tier ladders in `tier_for()` (replaced with live tier×type)
- Hardcoded buy-down hit rate strings (already done 2026-06-17)
- The `lock`/`secondary_lock` overloaded fields (renamed to `nrfi_lock`/`yrfi_lock` for clarity)
- Bare `score` fields on top-level pick objects (all namespaced)
- POTD candidate construction from non-dimension objects

---

## 7. Success criteria

The refactor is done when:

1. **A daily card can ship without manual intervention** for 14 consecutive days. The pipeline's POTD is consumed as-is, no swaps.
2. **Number-attribution validator catches ≥95% of hallucinated numbers** in a 30-game test set. Measured by manual review of flagged narratives.
3. **No object on the published card has a bare `score` field.** All numeric fields are namespaced. Pass: grep `\"score\":` across served jerry_cache rows = 0 results.
4. **PRIME label appears in exactly one calculation path per pick type.** Pass: grep for `= 'PRIME'` returns ≤ 3 sites (one per pick type) plus their callers.

---

## 8. Why this matters

Every day we manually intervene is a day the system isn't ready to ship to paying users. The architectural debt **is the launch blocker** — not the model accuracy (which is healthy at 58% direction over 30d), not the UX, not the data feeds. It's the labeling layer's clarity.

When this refactor lands, the engine speaks for itself: PRIME means one thing, sweat scores are dimension-scoped and namespaced, Jerry only cites verifiable numbers, and no one (human or AI) can misread a NRFI lock as an ML conviction.

That's when daily intervention drops to zero and the engine is truly ready for autonomous operation.
