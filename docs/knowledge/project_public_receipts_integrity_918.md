---
name: project-public-receipts-integrity-918
description: 🚨 P0 INTEGRITY. Every user-visible pick must be immutably logged to public_receipts. Historical record backfill from prop_jerry_reads + jerry_reads + ledger_snapshots + daily_degen. Aggregators rewrite to read from receipts only. Discovered 2026-09-18 — daily/surface records were inflated by tier=PRIME rows never published to users
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-18T05:01:34.989Z
---

**Andy directive (2026-09-18 ~2AM ET):**
> "Each pick needs to be logged and stored somewhere so if anybody ever asks well show us the games from where you keep the record... if we don't track it how are we supposed to calibrate models and assess patterns... if someone said what were your picks on august 18th we should go to dataset to see. This cannot be happening."

**Discovered:** `surface_records.prop_prime` (Receipts tab source) counts every prop with `tier=PRIME` that passed ban+juice filters — including 95% of picks that never made it to any user surface. Today (2026-09-17):
- 128 tier=PRIME props graded
- Only **6 actually published** (via publish_lock on prop_jerry)
- App shows "PRIME 82.8% +263u ROI 47.1% (30d)" — inflated ~5x by unpublished internal-tier picks
- Andy posted a "97% PRIME today" record externally — built on the inflated aggregate

**Composers currently inconsistent on publish_lock:**
- prop_jerry: 161 locks today ✅
- sweat_card: 6 (only some picks, not full top-8) ⚠
- sharp_card: **0** ❌
- ledger: 10 (chalk_parlay + prime_teased_single) ✅
- POTD: partial ⚠
- dawg, ladder, daily_degen: 0 ❌

**Historical composition data (backfill source):**
- prop_jerry_reads: **9,294 MLB rows** 2026-07-31 → present (per-prop user-visible reads)
- jerry_reads: **648 MLB rows** 2026-07-30 → present (game POTD/side/total)
- ledger_snapshots: 2026-08-18 → present
- daily_degen: exists with legs+result
- sharp_card composition + sweat_card composition — need to locate

## Architecture

**New table `public_receipts`** — universal immutable pick log.
Every user-visible pick lands here, permanently. Aggregators read FROM
receipts, not from raw pipeline tables.

Schema in `supabase/migrations/20260918a_public_receipts.sql`
(shipped 2026-09-18).

Fields per row: sport, surface, market, game_date, published_at,
player_name, prop_type, pick_side, pick_line, pick_odds, matchup,
tier, conviction, result, actual_value, source_table, source_id.

Immutable via UNIQUE(sport,surface,game_date,source_id) + trigger
that blocks UPDATE on pick fields (only result / actual_value / graded_at
can be filled in post-game).

## Phases

**Phase 1 — Backfill (non-destructive):**
- Populate public_receipts from prop_jerry_reads, jerry_reads,
  ledger_snapshots, daily_degen
- Do NOT overwrite existing surface_records — keep inflated numbers
  visible so Andy can see the delta between inflated and true

**Phase 2 — Side-by-side report:**
- New view/table `public_receipts_vs_current_records`
- Shows: for each surface + tier + window, "current app displays X-Y
  (Z%)" vs "receipt-verified X-Y (Z%)"
- Andy reviews before flipping the aggregator source

**Phase 3 — Aggregator swap (Andy-gated):**
- Rewrite compute_surface_records._pick_prop_tier() to read from
  public_receipts (with the ban/juice filters still applied)
- Same for pick_sharp, pick_potd, pick_dawg, pick_ledger, pick_ladder
- surface_records now displays receipt-verified records only

**Phase 4 — Forward-only lock enforcement:**
- Every composer (sweat_card, sharp_card, prop_jerry, potd, dawg,
  ladder, ledger, daily_degen) writes to public_receipts at publish
  decision moment
- Wired into cron so composers can never skip the receipt

**Phase 5 — Public verification endpoint:**
- Read-only API/view exposing "receipts for {date}" — anyone can pull
  the JSON of every pick on any given date with graded outcome
- Brand claim: "our record is 100% verifiable"

## Why this matters beyond brand

Model calibration + cohort work + LR overrides + all future SOTA-rebuild
phases depend on knowing WHICH picks users actually saw. Currently
signal_attribution grades ALL tier=PRIMEs — including the 122/day that
never published. The picks users see may perform differently from the
picks that never publish (juice discipline, family bans, composer-stage
filtering all narrow the pool). Without receipts, every model tune is
grading against a population that doesn't match production.

## Files (as shipped)

- `supabase/migrations/20260918a_public_receipts.sql` — schema + trigger
- `mlb_pipeline/backfill_public_receipts.py` — backfill from all sources
- `mlb_pipeline/receipts_report.py` — side-by-side vs current records
- Later: `mlb_pipeline/write_receipt.py` — shared receipt writer used by
  all composers (mirror of prop_publish_lock pattern)

## Related

- [[project_nfl_sota_rebuild_917]] — all phases depend on receipts for
  clean shadow-vs-live backtest
- [[feedback_sample_size_with_pct]] — receipt discipline is the
  operational form of "every % shows n"
- publish_lock migration 20260917e — the first-cut publish tracking
  that this project generalizes and enforces universally
