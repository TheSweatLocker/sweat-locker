# Cohort Rotation Policy — Design

**Status:** Draft / 2026-06-11
**Author:** ag + claude
**Related:** [refresh_cohort_signals.py](../mlb_pipeline/refresh_cohort_signals.py), [track_cohort_lifecycle.py](../mlb_pipeline/track_cohort_lifecycle.py)

---

## 1. Problem

Today's cohort engine is **binary in/out** with no concept of rule maturity or trust hierarchy.

A rule with `raw_n=12` at LOCK (75%+ shrunken_pct) fires identically to a rule with `raw_n=200` at LOCK. They contribute the same conviction delta (+18), they get the same tier label, they survive the same recency veto. **But they are not equally trustworthy.**

Specific failure modes the current design allows:

- **Probation-skipping.** Brand-new rules with `raw_n=10` emit immediately at whatever tier their shrunken pct lands. A 11-0 (100%) cohort shrinks to ~77.5% with PRIOR_N=30 — that's LOCK. Fires the same as a 150-15 (91%) rule that has been stable for months.
- **No middle state.** A rule with 14pp 30d drift is "stable" (15pp veto threshold). A rule with 15.5pp drift gets dropped entirely. There's no tier-down for mild drift, no probation for new evidence.
- **No staleness concept.** A cohort that fired 80 games last year but hasn't matched any games in the last 60 days is still treated as fully active.
- **Decay-mode blind.** A LOCK rule that slowly degrades over 90 days (LOCK → STRONG_EDGE → LEAN) reads identically to one that flash-decays in 7 days. The first is regression-to-mean; the second is a structural shift.
- **No conviction-by-sample weighting.** Two LOCK rules fire with equal weight in the audit_pool/conflict math regardless of n=12 vs n=300.

The cost of these in practice: small-sample noise drives borderline POTD picks, and we have no automated way to surface "this rule is dying" or "this rule is brand new — don't trust the loud number yet."

---

## 2. Goals

1. **Probation for new rules.** New rules earn their tier by accumulating samples, not by lucky early hit-rate.
2. **Graduated drift response.** Mild drift → tier-down, not silent passthrough. Sharp drift → veto (current behavior).
3. **Sample-weighted conviction.** A 200-game LOCK should outweigh a 25-game LOCK at the same tier in the audit_pool sort.
4. **Staleness retirement.** Rules that haven't matched a game in 60+ days lose ACTIVE status, return to PROBATIONARY when they next fire.
5. **Backwards-compatible.** Add states + caps to the existing rules list; don't rewrite the engine.

Out of scope (for v1):
- Per-cohort A/B testing of state thresholds
- ML-learned probation periods (just use a fixed sample threshold)
- Cross-cohort interaction (rule A modifies rule B's trust)

---

## 3. Rule State Machine

Each rule lives in exactly one state:

```
                         ┌──────────────┐
       new (n=10-24) ───▶│ PROBATIONARY │
                         └─────┬────────┘
                               │ n≥25 + 14d age
                               ▼
                         ┌──────────────┐
       ┌────────────────▶│   ACTIVE     │◀──────────────┐
       │                 └─────┬────────┘               │
       │                       │                        │
       │ no fire 60d           │ 7-14pp drift           │ drift recovers
       │                       ▼                        │
       │                 ┌──────────────┐               │
       │                 │   DRIFTING   │───────────────┘
       │                 └─────┬────────┘
       │                       │ 15pp+ drift same-sign
       │                       ▼
       │                 ┌──────────────┐
       └─────────────────│    STALE     │
                         └─────┬────────┘
                               │ next fire
                               ▼
                       (back to PROBATIONARY)

       15pp+ drift opposite-sign ────────────▶ ┌─────────┐
                                               │ REVERSED│  (drop, but log)
                                               └─────────┘

       60d sustained NEUTRAL ─────────────────▶ ┌─────────┐
                                                │ RETIRED │  (drop, manual revive)
                                                └─────────┘
```

### States in detail

**PROBATIONARY** — new rule, raw_n=10-24 OR returning from STALE.
- Emits at tier capped at LEAN regardless of shrunken_pct
- Logged separately in cohort_signals output for visibility
- Graduates to ACTIVE when `raw_n >= 25` AND `age_days >= 14`

**ACTIVE** — battle-tested rule.
- Emits at its natural tier (LOCK / STRONG_EDGE / LEAN / etc.)
- Full conviction delta applied
- Sample-weighted (see §4)

**DRIFTING** — recency in middle band (7pp-14pp 30d drift, same sign).
- Tier-down ONE notch (LOCK → STRONG_EDGE, STRONG_EDGE → LEAN, LEAN → NEUTRAL/skip)
- New visible signal — not silently passed through like today
- Returns to ACTIVE when drift falls below 7pp

**STALE** — rule hasn't matched any games in 60+ days.
- Not emitted
- Re-emits as PROBATIONARY when it next matches a game
- Tracks `last_fired_date` for staleness check

**REVERSED** (terminal-ish) — 15pp+ drift with opposite-sign direction.
- Dropped (matches current veto behavior)
- Logged with `reversal: true` flag for audit
- Re-emerges only if a future refresh brings 30d direction back into alignment

**RETIRED** (terminal) — 60 days sustained NEUTRAL (shrunken between 45-60).
- Dropped from emission
- Listed in a `retired_rules` sidecar so we can manually revive if conditions change
- Stops feature noise from forever-borderline rules

---

## 4. Sample-Weighted Conviction

Currently `conviction_delta` is a fixed lookup per tier (LOCK=+18, STRONG_EDGE=+10, etc.). Two LOCK rules at n=15 and n=200 both contribute +18.

**Proposed:** Multiply delta by a sample-weight factor.

```python
def sample_weight(n):
    # log-scaled curve. n=25 ≈ 0.55, n=50 ≈ 0.70, n=100 ≈ 0.85, n=200 ≈ 1.0
    if n < 10: return 0.0
    if n >= 200: return 1.0
    return round(math.log(n) / math.log(200), 2)
```

Applied at consumption time, NOT in cohort_signals (preserve raw conviction delta for audit). The audit_pool sort and resolver's cohort net counting both apply this weight:

```python
weighted_count = sum(sample_weight(rule['raw_n']) for rule in matched_strong_edge_rules)
```

A game with 10 STRONG_EDGE matches all at n=15 → weighted count ~3.5
A game with 5 STRONG_EDGE matches at n=150 → weighted count ~4.7

This penalizes "cohort net stacking" from small-sample rules without throwing them out entirely.

---

## 5. Staleness Tracking

Add a `last_fired_date` field to each rule, persisted across runs. On every refresh:

1. For each rule, if the rule matched ≥1 game in the last 60 days → update last_fired_date to today
2. If `today - last_fired_date > 60 days` → mark STALE
3. STALE rules don't emit but stay in the rules dict so we don't lose their history

This requires tracking which rules match which games — currently the tally pass only counts hits/losses, not which rule fired on which game. **Cheapest implementation:** stamp last_fired_date as max(game_date) of any game in last 60 days that contains the cohort's matches_if features.

Already most of what `tally()` does — just need to track the max game_date observed per (play, ck, dir) and persist it.

---

## 6. Implementation Plan

### Phase 1 (this week)

1. Add `state` field to each rule in `refresh_cohort_signals.py` output
2. Implement PROBATIONARY / ACTIVE state classification based on `raw_n`
3. Cap PROBATIONARY rules at LEAN tier in the output
4. Update `track_cohort_lifecycle.py` to log state transitions as new event types (`graduated`, `entered_drift`, etc.)

### Phase 2 (next week)

5. Add `last_fired_date` tracking (track max game_date during tally)
6. Add STALE detection (>60d no fire → STALE state, no emission)
7. Add DRIFTING state for 7-14pp drift, with tier-down at emission

### Phase 3 (after forward-test)

8. Implement `sample_weight()` at consumer sites:
   - `play_of_day._derive_cohort` and resolver gates
   - `signal_resolver._classify_cohort_net` deviation calculation
9. Add RETIRED state for sustained NEUTRAL

### Phase 4 (post-launch)

10. Audit dashboard surfacing state distribution (How many ACTIVE vs PROBATIONARY today? How many DRIFTING?)
11. Manual revive path for RETIRED rules when an analyst sees them re-emerge
12. Per-state hit-rate audit (do PROBATIONARY rules actually fire at predicted rates once they graduate?)

---

## 7. Open Questions

- **Probation threshold.** Why n=25? Picked because shrinkage with PRIOR_N=30 stops materially moving the posterior past ~n=25 (50/50 weight). Worth testing n=20 and n=35.
- **Drift bands.** 0-7pp / 7-14pp / 15pp+ is plausible but arbitrary. Calibrate against historical rule decay patterns once we have a year of lifecycle data.
- **Sample-weight curve.** `log(n)/log(200)` is one choice. Could also use `min(1, n/100)` (linear), or a posterior-variance-based weight. Pick one and forward-test.
- **STALE return path.** When a STALE rule re-fires, does it go to PROBATIONARY (full reset) or DRIFTING (since it was once trusted)? Lean PROBATIONARY — staleness suggests structural change.
- **RETIRED resurrection.** Should an analyst be able to manually flag a RETIRED rule for re-eval? Probably yes for v1.

---

## 8. Risks

- **Over-cap on PROBATIONARY.** If we hard-cap new rules at LEAN, we lose early signal from genuinely loud emergent rules (e.g., a new "umpire X under-friendly" rule that's 8-1 in its first 9 games). Trade-off: false-positive protection > emergent signal capture.
- **Weight compression.** If sample_weight maxes at 1.0 at n=200 and the median rule has n=50, weighted counts will be compressed toward 0.7. Resolver thresholds may need rebaseline.
- **Cron cost.** Phase 2 needs last_fired_date tracking across the full tally. Estimated +30s on refresh_cohort_signals (currently ~2 min). Acceptable.
