# Public Splits Schema v2 + Cross-Sport Source Expansion (Proposal)

**Status:** Design proposal. Not implemented. Review before dedicated build session.
**Date:** 2026-08-22
**Trigger:** User asked for "index-driven backend swap" so new splits sources can be added with minimal app changes.

## Current architecture

### `public_splits_archive` — pivoted-by-source
```sql
CREATE TABLE public_splits_archive (
  id BIGSERIAL PRIMARY KEY,
  sport TEXT, game_id TEXT, market TEXT, pick_side TEXT,
  oc_money_pct NUMERIC, oc_bets_pct NUMERIC, oc_divergence NUMERIC,
  fr_handle_pct NUMERIC, fr_bettors_pct NUMERIC,
  current_line NUMERIC, current_odds INT,
  captured_at TIMESTAMPTZ
);
```
**Problem:** each source has dedicated columns. Adding scoresandodds/pinnacle/etc. = schema change + app change. Doesn't scale.

### `fadereport_signals`, `cleatz_signals` — source-specific tables
Separate tables per source. Same "add-source = new-table + new app reader" problem.

### App reads today
- `game_context.oddscrowd_snapshot` JSONB (OC-specific)
- Raw signals via `fadereport_signals` / `cleatz_signals` queries
- No unified source-agnostic read path

## Proposed v2 architecture

### New table: `public_splits_v2` (long-form normalized)
```sql
CREATE TABLE public_splits_v2 (
  id BIGSERIAL PRIMARY KEY,
  snapshot_ts TIMESTAMPTZ NOT NULL,
  sport TEXT NOT NULL,
  game_id TEXT NOT NULL,
  market TEXT NOT NULL,          -- 'ml' | 'rl' | 'total' | 'spread'
  side TEXT NOT NULL,            -- 'HOME' | 'AWAY' | 'OVER' | 'UNDER'
  source TEXT NOT NULL,          -- 'oc' | 'fr' | 'cz' | 'so' | 'pinnacle' | ...
  metric TEXT NOT NULL,          -- 'money_pct' | 'bets_pct' | 'handle_pct' | 'divergence' | 'strength'
  value NUMERIC,
  UNIQUE (game_id, market, side, source, metric, snapshot_ts)
);

CREATE INDEX ON public_splits_v2 (sport, game_id, snapshot_ts DESC);
CREATE INDEX ON public_splits_v2 (source);
```

Adding a new source = insert rows. No schema change. No app change.

### New computed column: `game_context.splits_summary` (JSONB)
Written per-game after all splits sources land. Backend aggregates:
```json
{
  "captured_at": "2026-08-22T14:00:00Z",
  "sources_present": ["oc", "fr", "cz", "so"],
  "ml": {
    "home": {"money_pct_avg": 65, "bets_pct_avg": 71, "sources_agree": 3},
    "away": {"money_pct_avg": 35, "bets_pct_avg": 29, "sources_agree": 1}
  },
  "rl": {...},
  "total": {...},
  "triple_confirmed": ["ml_home"],   // 3+ sources agreeing on sharp side
  "dissent_flags": ["oc_dissents_ml"]  // OC opposite of majority
}
```
**App reads this ONE blob** — no source-specific logic. New source lands → aggregator recomputes → app sees updated summary without a code change.

### Cross-sport pull infrastructure

Refactor `pull_externals_*` scripts into ONE universal `pull_splits_universal.py` (or per-source):
```
pull_scoresandodds.py --sport ALL
pull_fadereport.py --sport ALL      (extends today's MLB-only version)
pull_cleatz.py --sport ALL
pull_oddscrowd.py --sport ALL       (already multi-sport internally, just extend archive writer)
```

Each writes to `public_splits_v2` uniformly. Missing sources for a given sport → their rows just don't get written; aggregator handles gracefully.

## Rollout phases

### Phase 1: schema + backfill (~2 hrs, safe)
1. Migration to create `public_splits_v2`
2. Backfill from `public_splits_archive`, `fadereport_signals`, `cleatz_signals`
3. `game_context.splits_summary` column + writer (runs alongside existing writes — no cutover yet)

### Phase 2: scoresandodds scraper (~3 hrs)
1. Build `pull_scoresandodds.py` with BeautifulSoup DOM traversal
2. Target their consensus-picks pages per sport
3. Handle 6 sports (MLB, NFL, NCAAF, NCAAB, NBA, NHL)
4. Write to `public_splits_v2` directly

### Phase 3: extend FR + CZ to more sports (~2 hrs each)
1. FR: currently only MLB — extend to NFL/NCAAF (has data), verify NBA/NHL/NCAAB coverage
2. CZ: currently NFL/MLB/CFB — check if they cover NBA/NHL/NCAAB
3. Update pullers to iterate sports

### Phase 4: app cutover (~1 hr, requires coordination)
1. App switches to read `game_context.splits_summary` JSONB
2. Deprecate source-specific reads
3. Drop old-column reads from `public_splits_archive`

### Phase 5: externals extension (~2 hrs per sport)
Same pattern for `external_picks` — extend `pull_externals_*.py` per sport to hit all 11 major aggregators. Or route through OC (which itself aggregates ~13 handicappers) to get coverage for free.

## Cross-sport source coverage matrix (target)

| Source | MLB | NFL | NCAAF | NCAAB | NBA | NHL | UFC |
|---|---|---|---|---|---|---|---|
| OddsCrowd | ✅ | 🎯 | 🎯 | 🎯 | 🎯 | 🎯 | ❌ (n/a) |
| Fadereport | ✅ | ✅ | ✅ | 🎯 | 🎯 | 🎯 | ❌ |
| Cleatz | ✅ | ✅ | ✅ | 🎯 | 🎯 | 🎯 | ❌ |
| **ScoresAndOdds** | **🎯** | **🎯** | **🎯** | **🎯** | **🎯** | **🎯** | ❌ |

🎯 = target coverage after this build. Every sport gets at least 3 sources → triple-confirm signal works cross-sport.

## Verifier

Extend `verify_signal_wiring.py` (or new `verify_splits_coverage.py`) that checks per sport:
- ≥3 sources firing today
- No dead ctx.X references from splits-consuming signals
- `game_context.splits_summary` populated for all games in slate

## Total estimated effort: ~14-18 hours

Best done as a dedicated 2-day sprint before NCAAB season (Nov 3):
- Day 1: Phases 1 + 2 (schema + scoresandodds)
- Day 2: Phases 3 + 4 + 5 (extend sources + app cutover + externals)

## Related

- [pipeline_workflow_split_proposal.md](./pipeline_workflow_split_proposal.md)
- Memory: `project_pipeline_audit_822`, `project_dissent_audit_822`
