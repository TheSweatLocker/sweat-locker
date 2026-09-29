---
name: project_prop_l5_leak_922
description: "🚨 2026-09-22: L5/L10 lookback included the game being predicted, feeding the LR model that sets prop tier. All PRIME prop history before 9/22 is invalid. Fixed with before_date bound; LR tier authority revoked pending retrain."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-22T17:03:32.305Z
---

**The prop record was never real.** Root-caused 2026-09-22 after Andy:
"what is creating prime props after and calling them wins, thats
poisonous."

## Mechanism

`backfill_prop_lookback.fetch_mlb_player_recent` pulled a player's whole
season gameLog, sorted newest-first, returned `vals[:n]` — **no upper
date bound**. It enriches a prop for a given `game_date`, so once that
game was played it became the newest entry and sat inside its own
L5/L10 window.

`player_l5_hit_count` / `l10` / `season_hit_pct` are inputs to the LR
model that **overrides tier**. So tier was set partly by the outcome it
was predicting.

    l5_hit_count = 3  ->    6-19    24.0%
    l5_hit_count = 4  ->  966-239   80.2%
    l5_hit_count = 5  ->  308-4     98.7%   <- tautology

"5 of last 5 hit" ⇒ the predicted game hit ⇒ already won.

## What was NOT wrong (check this order next time)

Ruled out before blaming selection:
* **Outcomes real** — `final_value` reconciled vs MLB box scores:
  9/20 1,337 match / 0 mismatch; 9/16 1,219 / 0; 9/15 1,394 / 1.
* **Grading correct** — 16,507 grades recomputed from
  `final_value` vs `prop_line`: ZERO disagreements.
* Not late row creation — PRIME created after the game did *worse*
  (79.8%) than created on game day (83.7%).
* Not duplicates — only 335 dup rows in 18,105.

## Fingerprints worth reusing

* **Edge grew as market certainty fell**: PRIME beat implied by +8.6 at
  ≤-250 rising monotonically to **+31.6** at ≥-110. Real skill does not
  scale inversely with market confidence — leakage does.
* **Control group**: never-enriched rows sit at −1.4 vs market
  (efficient/honest); enriched at +19.3. Same generator, same grading.
* **Unders ≫ overs** across every family was a red herring (rare events
  are genuinely under-heavy) — the odds-conditioned comparison is what
  isolated it.
* Timing: 12,474 of 12,498 enriched rows had lookback written AFTER
  first pitch.

## Fixes shipped (20b443cc)

* `before_date` bound on `fetch_mlb_player_recent` + the rows variant
  (the latter feeds the user-facing L5/L10 table, which was displaying
  the result of a still-open prop). Rows with no date are dropped too.
* **LR tier authority revoked by default** — `MLB_PROP_LR_TIER=on` to
  restore. Bounding the fetch stops new leakage but coefficients were
  fit to a feature that contained the answer, so they over-trust it.
  `models/mlb_prop_logreg.json` needs retraining on clean history; its
  advertised "PRIME 85.6% on holdout" measured the leak.

## Standing consequence

**No valid PRIME prop record exists before 2026-09-22.** Do not quote
prop tier hit rates from before that date — not in the app, not in
socials, not in calibration. Today was the first clean day (159 rows
enriched pre-first-pitch, 0 after).

Also note `public_receipts` is 11,233 `reconstructed` vs 38 `live` — the
whole surface-record layer is post-hoc. See
[[project_public_receipts_integrity_918]] and
[[feedback_surface_records_trust_levels]], which flagged "prop_prime
inflated" before the cause was known. Related:
[[project_card_lineage_calibration_921]] (its 57.7% props figure came
from publish-locked receipts and was the *honest* number all along).
