---
name: project-vault-dissent-patterns-902
description: "9/2 design for Vault Match external-source dissent patterns (MAJ_when_CZ_dissents +16pp, 3_of_3_agree fade). Data source verified, build queued post-launch."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-02T21:49:32.200Z
---

🎯 **9/2 design locked, build queued.** External-source dissent
patterns from `project_dissent_audit_822`:
- **MAJ_when_CZ_dissents +16pp winner** (when CZ sharp side ≠ majority, MAJ wins)
- **MAJ_when_OC_dissents 77%** (per `project_per_source_tracker_moat_818`)
- **3_of_3_AGREE -11pp fade** (when all 3 sources agree, fade the consensus)

**Why:** Highest-conviction Vault Match patterns per memory findings.
Current sharp-confirmed patterns audit at coin-flip; dissent patterns
have real +/-16pp edge according to graded history.

**How to apply:** Extends Vault Match `PATTERN_CATALOG`. Same
guardrail stack (n≥15, hit%≥65%, Wilson≥55%, shadow-mode default).

## Data source (verified 9/2)

**Table**: `public_splits_v2` (raw per-source snapshots)
- Columns: `game_id, market, side, source, metric, value, snapshot_ts, sport`
- Sources for MLB: `so, cz, oc, fr` (all 4 splits providers)
- Metrics: `bets_pct`, `money_pct` per source/market/side
- Per-source "sharp side" derivable: side with (money_pct > bets_pct)
  OR side with highest money_pct

**NOT in**: `external_picks` (those are handicapper picks, different domain).

## Build shape (post-launch, ~2-3 hours)

**Step 1: dissent rollup script**
`mlb_pipeline/compute_split_dissent_rollup.py` runs nightly:
- Queries `public_splits_v2` + joins to `{sport}_game_results`
- For each (game, market): compute each source's sharp side (money%>bets%)
- Determine majority sharp side (2-of-3 or 3-of-3)
- Tag: `agreement='TRIPLE'|'MAJ_2/3'|'SPLIT'`, `dissenter=source_or_null`
- Write to new `split_dissent_snapshots` table

**Step 2: register new patterns in `compute_sport_patterns.py`**
```python
{
  'sport': 'MLB', 'key': 'mlb_maj_when_cz_dissents',
  'direction': 'BACK', 'label': 'MAJ · CZ Dissents',
  'lookback_days': 90,
  'matches': lambda g: g.get('split_dissent_agreement') == 'MAJ_2/3' and g.get('split_dissenter') == 'cz',
  'outcome': lambda g: outcome_majority_side(g),  # W if majority sharp side won
},
{
  'sport': 'MLB', 'key': 'mlb_all_3_agree_fade',
  'direction': 'FADE',
  'matches': lambda g: g.get('split_dissent_agreement') == 'TRIPLE',
  'outcome': lambda g: outcome_faded_side(g),  # W if consensus LOST
},
# Same shape for oc/fr dissenters
```

**Step 3: extend compute_sport_patterns.fetch_games**
- Merge split_dissent_snapshots into game dicts during fetch
- Cache-friendly (rollup is small — dozens of rows per day)

**Step 4: validate**
- Run compute + verify hit_pct matches memory (16pp lift, 77%, -11pp fade)
- If validates → shadow-mode auto-attaches, guardrail decides render

**Step 5: badge render**
- Zero code changes needed — existing Vault Match render picks up new
  patterns automatically once they clear guardrails

## Cross-references

- [[project-vault-match-901]] — parent Vault Match infra
- [[project-vault-match-guardrails-901]] — n>=15 + Wilson floor
- [[project-per-source-tracker-moat-818]] — source calibration foundation
- [[project-dissent-audit-822]] — original findings

## Timeline

Week 1 post-launch. Priority relative to other Vault expansion:
- Ship dissent-rollup + 3 patterns
- Verify against memory's stated hit rates
- If validates, flip Vault Match shadow flag ON for MLB
