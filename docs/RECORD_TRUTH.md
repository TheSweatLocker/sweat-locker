# RECORD_TRUTH — The Authoritative Source-of-Truth Reference

**Purpose**: Every W-L number visible in the app comes from one place. This
doc says exactly where, exactly what filter, exactly what math. If a
displayed number doesn't match this doc, the doc is right and the code is
wrong.

**Frozen**: 2026-08-28. Any change to spec below requires:
1. A migration commit that renames the changed thing
2. A version bump on `surface_records.last_computed_at`
3. An entry in the CHANGELOG at the bottom of this file

---

## Global rules

| Rule | Value |
|---|---|
| Result-value normalization | `Win`/`W` → win, `Loss`/`L` → loss, `Push`/`P` → push, else null |
| Aggregator | `mlb_pipeline/compute_surface_records.py` |
| Read location for app | `surface_records` table |
| Composite PK | `(sport, surface, window_key)` |
| Windows emitted | `mtd` (calendar month), `d7` (last 7d), `d30` (last 30d), `epoch` (since `SHARP_RECORD_EPOCH`), `lifetime` (all-time) |
| `SHARP_RECORD_EPOCH` | `2026-08-20` (defined in compute_surface_records.py) |

---

## Surface 1: SHARP (jerry_reads → sides)

| Field | Value |
|---|---|
| Source table | `jerry_reads` |
| Ship filter | `sport='MLB' AND conviction >= 60 AND result IS NOT NULL` |
| **Epoch cutoff** | **`game_date >= 2026-08-20` — applied to ALL windows including `lifetime`** |
| Grading source | `jerry_reads.result` (values: `Win`/`Loss`/`Push`/`NO_ACTION`/null) |
| Stake | **1u flat** (jerry_reads has no `tier` column) |
| Payout | **0.909 flat** (jerry_reads has no book snapshot; call_odds_est unreliable) |

### ⚠️ Findings

**BUG-S1** — Sharp lifetime is truncated to epoch. Raw MLB conv≥60 all-time
= 87-68-3. `surface_records.sharp.lifetime` = 32-23-2. The 55-45 pre-epoch
history is dropped.

- Root cause: `pick_sharp()` line 129 hard-codes `if d < SHARP_RECORD_EPOCH: continue`
- Impact: user picks "ALL" (lifetime) in Receipts, sees the same number
  as "Fresh" (epoch). MTD chip also collapses to epoch.
- **Design question for user**: is this correct? Pre-8/20 sides had
  flat-110 assumption baked in and the record was reset. If we want
  lifetime to include pre-8/20, we need to reinterpret those rows.

### Current numbers (verified)

| Window | Raw | surface_records | Match |
|---|---|---|---|
| d7 | 29-19-1 +7.36u (60.4%) | 29-19-1 +7.36u | ✓ |
| epoch | 32-23-2 +6.09u (58.2%) | 32-23-2 +6.09u | ✓ |
| mtd | 32-23-2 +6.09u (58.2%) | 32-23-2 +6.09u | ⚠ raw would be 73-60 without epoch cutoff |
| d30 | 32-23-2 +6.09u (58.2%) | 32-23-2 +6.09u | ⚠ raw would be 87-68 |
| lifetime | 32-23-2 +6.09u (58.2%) | 32-23-2 +6.09u | ⚠ raw would be 87-68 |

---

## Surface 2: PROP (mlb_pipeline_props)

| Field | Value |
|---|---|
| Source table | `mlb_pipeline_props` |
| Ship filter | `tier IN ('PRIME','STRONG') AND conviction > 0 AND result IS NOT NULL` |
| Grading source | `mlb_pipeline_props.result` (values: `Win`/`Loss`/`Push`) |
| Stake base | `PRIME=2.0`, `STRONG=1.5` (from `TIER_UNITS`) |
| Stake halving | odds ≤ -180 OR odds ≥ +250 → `base * 0.5` |
| Payout | `book_over_odds` (if `direction='over'`) or `book_under_odds` (if `direction='under'`), fallback 0.909 |

### ⚠️ Findings

**No bugs today** — aggregator perfectly matches raw across all windows.

**Known context**:
- L10 gate demoted 1000 historical `hits_over` PRIME/STRONG rows to `COVERAGE` on 8/28
- Post-demote lifetime PRIME/STRONG = 1013-792-13 with +21.35u (56%)
- Halving on juice now works (previous odds-column bug fixed 8/28)

### Current numbers (verified)

| Window | Raw | surface_records | Match |
|---|---|---|---|
| d7 | 25-17 +1.57u (60%) | 25-17 +1.57u | ✓ |
| epoch | 31-18 +7.72u (63%) | 31-18 +7.72u | ✓ |
| mtd | 127-110 −36.35u (54%) | 127-110 −36.35u | ✓ |
| d30 | 136-125 −52.57u (52%) | 136-125 −52.57u | ✓ |
| lifetime | 1013-792-13 +21.35u (56%) | 1013-792-13 +21.35u | ✓ |

---

## Surface 3: LADDER (ladder_rung)

| Field | Value |
|---|---|
| Source table | `ladder_rung` |
| Ship filter | `sport='MLB' AND result IS NOT NULL AND conviction != 0 AND tier != 'COVERAGE'` |
| Grading source | `ladder_rung.result` (values: `Win`/`Loss`/`Push`) |
| Stake | 1u flat |
| Payout | `odds_american`, fallback 0.909 |

### ⚠️ Findings

**No bugs today** — aggregator matches raw. My audit script initially
disagreed by 2 losses because it treated `conviction=NULL` as `conviction=0`
and skipped those rows. Aggregator correctly includes conv=NULL rows
(id=1 Milwaukee 8/19, id=13 NYY 8/27) — real ladder rungs, just missing
a conviction stamp.

### Current numbers (verified)

| Window | surface_records |
|---|---|
| d7 | 2-4 −2.31u |
| epoch | 2-6 −4.31u |
| mtd | 2-7 −5.31u |
| d30 | 2-7 −5.31u |
| lifetime | 2-7 −5.31u |

---

## Surface 4: LEDGER (ledger_suggestions)

| Field | Value |
|---|---|
| Source table | `ledger_suggestions` |
| Ship filter | `sport_scope='MLB' AND result IS NOT NULL` |
| Grading source | `ledger_suggestions.result` (values: **`W`/`L`/`P`** single-letter) |
| Stake | 1u flat |
| Payout | `combined_odds`, fallback 0.909 |

### ⚠️ Findings

**Schema drift**: ledger uses `W`/`L`/`P` while every other table uses
`Win`/`Loss`/`Push`. Aggregator's `_classify` normalizes both formats so
data is correct, but this is a footgun — any new code reading
`ledger_suggestions.result` directly must handle both formats.

### Current numbers (verified)

| Window | surface_records |
|---|---|
| epoch | 2-0 +2.22u |
| mtd | 5-1 +4.29u |
| d30 | 5-1 +4.29u |
| lifetime | 5-1 +4.29u |

---

## Surface 5: POTD (daily_best_bet_history)

| Field | Value |
|---|---|
| Source table | `daily_best_bet_history` |
| Ship filter | `sport='MLB' AND result IS NOT NULL AND result != 'Pending'` |
| Grading source | `daily_best_bet_history.result` (values: `Win`/`Loss`/`Push`) |
| Stake | 1u flat |
| Payout | `odds_american` (populated by `play_of_day.py` writer since 8/27; migration `20260827c` backfilled historical MLB ML rows), fallback 0.909 |

### ⚠️ Findings

**Known drift** (documented 8/27 audit):
- **8/16** `Under 10.5` POTD has `odds_american=NULL` after backfill nulled it (was +120 inherited from Athletics ML close). Correct — Under totals default to -110 fallback.
- **8/21** `Pittsburgh Pirates RL 1.5` had `odds_american=-229` inherited from ML close, nulled 8/27. Fallback -110 applied.
- **8/22** `daily_best_bet_history.lean` says "San Diego Padres ML (Jerry 83)" but `jerry_cache.data.leanDisplay` says "NRFI — Score 89/100". Concurrent-write race in `play_of_day.py`. History has the graded pick. Cache has a different pick. **Requires per-day cache/history reconciliation before launch.**

### Current numbers (verified)

| Window | surface_records |
|---|---|
| d7 | 3-2 +0.33u |
| epoch | 5-2 +2.02u |
| mtd | 10-7 +1.57u |
| d30 | 11-7 +2.47u |
| lifetime | 54-43-4 +5.56u |

---

## Cross-surface headline math

The Sharp Card publicly-displayed headline (what people screenshot) sums
Sharp + Prop for the epoch window:

| | Sharp | Prop | HEADLINE |
|---|---|---|---|
| **Fresh (since 8/20)** | 32-23-2 +6.09u | 31-18 +7.72u | **63-41-2 +13.81u (61%)** |

Sub-line the app shows: `32-23 sides · 31-18 props`

---

## Design decisions (resolved 2026-08-28)

**Q1 answered: A** — Sharp lifetime stays truncated to epoch.
- Rationale: pre-8/20 sides record was measured with a flat -110 assumption
  that isn't defensible. Keeping the truncation means the "Sharp" number
  is always the honest post-recalibration record.
- App implication: `lifetime` chip for Sharp shows same number as `epoch`.
  Not a bug — a design choice. Doc + label must be transparent about it.

**Q2 answered: A** — Atomic POTD writes via stored procedure.
- Migration `20260828_potd_atomic_write.sql` creates `write_potd_atomic()`
  which upserts jerry_cache + daily_best_bet_history in one transaction.
- `play_of_day.py` calls the RPC. Fallback to two separate writes if the
  RPC is missing (migration not applied).
- Prevents future SDP/NRFI-style splits.

**Q3 answered: A** — Ledger schema normalized to `Win/Loss/Push`.
- Migration `20260828b_ledger_result_normalize.sql` UPDATEs historical
  `W`/`L`/`P` rows to full names.
- Aggregator's `_classify` handles both formats — historical rows update
  cleanly, no code churn required.

---

## Change log

| Date | Change | Author |
|---|---|---|
| 2026-08-28 | Doc created + initial 5-surface audit | System |
| 2026-08-28 | Q1/Q2/Q3 resolved; 20260828 + 20260828b migrations shipped; play_of_day RPC | System |
