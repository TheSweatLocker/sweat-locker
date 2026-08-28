# JERRY_UNIFICATION — Ensemble picks. Jerry narrates.

**Purpose**: Make it impossible by construction for Jerry's stored pick
to disagree with the ensemble's `primary_play`. Enforce the "narrator,
not picker" architecture across every sport.

**Written**: 2026-08-28. Enforced going forward.

---

## The design in one sentence

> `ctx.primary_play` is the authoritative pick for every game.
> `jerry_reads` is prose ABOUT that pick — never a competing opinion.

---

## Data flow (unified, all sports)

```
game_context (ensemble) → primary_play on ctx        [MECHANICAL PICK]
                                ↓
                    generate_{sport}_game_reads.py
                                ↓
                       Jerry LLM writes prose         [NARRATIVE ONLY]
                                ↓
                        jerry_cache.narrative         [prose storage]
                                ↓
                    sync_jerry_reads_from_ctx.py
                                ↓
       jerry_reads row {                              [DERIVED, ATOMIC]
         call_market  = primary_play.type
         call_side    = primary_play.side
         call_line    = primary_play.line
         call_text    = derived from side+team names
         short_read   = first sentence of narrative
         long_read    = full narrative
         conviction   = primary_play.conviction
       }
                                ↓
                            App reads               [BADGE + PROSE]
```

**Key property**: `jerry_reads.call_*` fields are DERIVED from primary_play,
not from LLM output. Drift is impossible by construction.

---

## Per-sport rollout status

| Sport | Prompt format | Sync path | jerry_pick_scrub wired | Status |
|---|---|---|---|---|
| MLB | Structured CALL block (legacy) + defer_call_to_ensemble overwrite | Direct write from parse_synthesis | ✓ (recompute cron) | Working but redundant |
| UFC | Structured CALL block (fight_synthesis) | Direct write from parse_synthesis | ✗ | Working |
| NFL | Prose only | Bridge via `sync_jerry_reads_from_ctx.py` | ✗ | Bridge deployed 8/28 |
| NCAAF | Prose only | Bridge via `sync_jerry_reads_from_ctx.py` | ✗ | Bridge deployed 8/28 |
| NCAAB | Prose only | Bridge (needs wiring) | ✗ | Pending |
| NBA | Prose only | Bridge (needs wiring) | ✗ | Pending |
| NHL | Prose only | Bridge (needs wiring) | ✗ | Pending |

---

## Migration to unified pattern (Phase 3 execution)

### 3a. Deprecate `defer_call_to_ensemble` in MLB / UFC
**Why**: it exists to overwrite Jerry's LLM CALL block with primary_play.
Under the unified pattern, Jerry shouldn't emit a CALL block at all —
prompt should be prose-only. Then defer_call becomes redundant with
sync_jerry_reads_from_ctx.

**Change**:
- Keep defer_call_to_ensemble for now (safety net) — mark as deprecated
- Update MLB Jerry prompt to prose-only (long-term); until then defer_call
  still fires because prompt still emits CALL block

### 3b. Universal sync deployment
**Why**: NCAAF/NFL/NCAAB/NBA/NHL all use the bridge script. UFC + MLB
should too — even though they have parse_synthesis paths that work,
sync_jerry_reads_from_ctx is the single source of truth.

**Change**: wire `sync_jerry_reads_from_ctx.py --sport {SPORT}` into
every sport's pipeline after their Jerry LLM step. Belt-and-suspenders:
if parse_synthesis works, sync just no-ops (same values). If parse
fails, sync writes correct values.

### 3c. Universal `jerry_pick_scrub` deployment
**Why**: recompute_primary_play flips picks post-Jerry-write. Scrub
runs post-recompute to re-sync CALL fields. Currently wired for MLB
only. NFL, NCAAF, NCAAB need it too.

**Change**: wire `jerry_pick_scrub.py --sport ALL` after every sport's
`recompute_primary_play` step.

### 3d. Prose lag policy
**Problem**: when primary_play flips post-morning-Jerry, badge updates
(via scrub) but prose still argues for old pick. Options:

- **A (recommended)**: accept prose lag, add UI label:  
  "Analysis reflects [X] AM read. Current pick reflects latest recompute."
- **B**: regen LLM for changed games (~$0.02/game, ~1-3 games/day).
  Cost: negligible (~$1/mo). Complexity: needs per-game trigger + rate limit.
- **C**: deterministic prose generator from primary_play + struct. No LLM.
  Cost: 0. Complexity: another template to maintain.

**Recommendation**: A now, revisit B after seeing user reactions.

### 3e. Enforcement test
Add a nightly test in `audit_data_accuracy.py`:
- For each game with `primary_play` on ctx today
- Verify `jerry_reads` row exists AND `call_market/side` matches primary_play
- FAIL if drift found → gets logged in DQ dashboard

Currently jerry_pick_scrub prevents drift; this test verifies the scrub
runs and works.

---

## Bugs Phase 3 fixes

1. **NCAAF/NFL/NBA/NHL Jerry never populated jerry_reads** — fixed by
   sync_jerry_reads_from_ctx (deployed for NCAAF 8/28, needs wiring
   for others).
2. **MLB Jerry can still emit picks that disagree with ensemble** —
   defer_call_to_ensemble overwrites. Not user-visible. Prompt refactor
   would clean up ~5% wasted LLM tokens.
3. **Post-recompute drift in non-MLB sports** — jerry_pick_scrub only
   wired for MLB. Fix in 3c.

---

## Open questions

**Q1** — Prose lag policy (3d above): A / B / C?

**Q2** — Wire universal sync (3b) tonight, or wait until per-sport
prompt refactor (3a)?

**Q3** — Add the audit test (3e) tonight, or defer to Phase 4 systems
streamline?

---

## Change log

| Date | Change |
|---|---|
| 2026-08-28 | Doc created + status per sport enumerated |
