---
name: project-vault-match-901
description: 9/1 Vault Match badge — proprietary system-detected patterns from graded history surfaced as game card chip (the moat play)
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-02T03:18:27.925Z
---

🎯 **9/1 Vault Match — the moat play.** System-detected patterns like
"MLB PRIME with 2+ sources sharp confirmed = 71% n=42" surfaced as a
game card badge. Uses our proprietary dataset (external_picks × ctx
× results) to find edges no one else has.

**Why:** User asked for a badge where "our proprietary datasets analyze
scenarios or patterns" and system identifies "a certain external record
plus model call" that historically hits. This is the differentiator.

**How to apply:** New patterns added via one entry in PATTERN_CATALOG
(mlb_pipeline/compute_sport_patterns.py). No schema change needed.
Silent-hide contract: chip renders only when a matched pattern clears
`MIN_N=15` AND `MIN_HIT_PCT=65%`.

## Architecture (all 4 pieces shipped)

**1. Schema** — `supabase/migrations/20260901p_sport_pattern_registry.sql`
- New table `sport_pattern_registry (sport, pattern_key, label, description, lookback_days, n_wins/losses/pushes, hit_pct, last_computed_at)`
- Rolling per-pattern stats. RLS: read public, service write.
- `supabase/migrations/20260901q_matched_patterns_column.sql` adds
  `matched_patterns JSONB DEFAULT '[]'` column to every sport's
  `*_game_context` table.

**2. Recompute script** — `mlb_pipeline/compute_sport_patterns.py`
- Nightly cron. Iterates PATTERN_CATALOG.
- For each pattern: fetch graded ctx × results in lookback window,
  count matches, compute hit%, upsert to sport_pattern_registry.
- `--sport MLB` / `--dry-run` flags.

**3. Attach script** — `mlb_pipeline/attach_vault_matches.py`
- Runs AFTER recompute + AFTER context builder. Per upcoming game
  (today-1d to today+8d), evaluates every PATTERN_CATALOG entry
  matching this sport, filters to patterns clearing threshold, sorts
  by hit_pct DESC, writes `matched_patterns=[{key,label,hit_pct,n,description}...]`
  back to game_context row.

**4. App render** — `app/index.tsx` game card chip block
- Reads `ctxAny.matched_patterns[]`, renders best-hit-rate pattern as
  `🎯 LABEL · HIT% · nN` chip (accent color). Prepended to sweat
  badge stack so it wins cap-slice tie-break (highest priority).
- Silent hide when empty.

## Workflow wiring

MLB (`.github/workflows/mlb_pipeline.yml`):
- `Recompute Vault Match patterns (MLB)` after external calibration
- `Attach Vault Matches to today's games (MLB)` after recompute

NFL (`.github/workflows/nfl_pipeline.yml`):
- `Recompute Vault Match patterns (NFL)` on full/resolver_only modes
- `Attach Vault Matches to upcoming games (NFL)` on full/context_only/resolver_only

## PATTERN_CATALOG (expanded 9/1 to 9 patterns)

Each entry has `direction` ('BACK' | 'FADE') persisted to
sport_pattern_registry.direction (migration 20260901s).

MLB (4):
- `mlb_sharp_confirmed_prime` — BACK — PRIME + triple_confirmed
- `mlb_sharp_confirmed_strong` — BACK — STRONG + triple_confirmed
- `mlb_confluence_backs_prime` — BACK — PRIME + |confluence_net|≥3 same-direction
- `mlb_public_overload_fade` — FADE — public ≥70% on pp side → fade

NFL (3):
- `nfl_home_div_dog` — BACK — home divisional dog
- `nfl_road_fav_7plus_fade` — FADE — road fav 7+ → back home cover
- `nfl_rest_edge_dog_back` — BACK — dog with 3+ day rest edge

NCAAF (2):
- `ncaaf_road_fav_10plus_fade` — FADE — road fav 10+ → back home
- `ncaaf_sp_underdog_edge` — BACK — SP+ favors market underdog by 3+

NBA / NCAAB / NHL: empty (season openers 10/22 / 11/3 / 10/7).

App render:
- BACK → 🎯 Vault Match (accent color)
- FADE → ⚠️ Vault Fade (warn color)

## To extend

1. Add entry to PATTERN_CATALOG with `sport`, `key`, `label` (≤14 chars for badge), `description`, `lookback_days`, `matches: lambda g: bool`, `outcome: lambda g: 'W'|'L'|'P'`
2. Next MLB/NFL cron populates stats
3. Attach step next cron writes to matched_patterns
4. Badge auto-renders when threshold cleared (no app change)

## Threshold rationale

- `MIN_N=15` — ~13pp SE at 65% hit rate, so 65% is meaningfully >50% at ~2σ
- `MIN_HIT_PCT=65%` — +30% ROI at -110 juice; well above chance
- `MIN_WILSON_LOWER=0.55` — 95% CI lower bound must clear 55% or skip render (guardrail added 9/1)
- `MIN_HIT_PCT_ABOVE_JUICE=52.4` — break-even at -110 juice; hard floor even if other thresholds loosen (guardrail 9/1)
- `STALE_HOURS=36` — freshness cutoff; skip if `last_computed_at` older (guardrail 9/1)
- All live in `attach_vault_matches.py` — bump post-launch if too permissive/restrictive

## Guardrail stack (added 9/1, commit cae3ba1c)

Before shipping expansion catalog, hardened the pipeline:

1. **Correctness fix** — `_pp_result_outcome` rewritten to use canonical
   grader fields (`pp.type` not `pp.market`; lowercase `spread_result`/
   `total_result`; `home_win` bool). Prior version would silently
   grade most patterns as all-P.
2. **Freshness + Wilson floor + vig floor** in attach; payload now
   carries `wilson_low`/`wilson_high`/`lookback_days`/`computed_hours_ago`
3. **Shadow mode** — `feature_flags(sport='ALL', feature='vault_render', enabled=false)`.
   Attach still runs + populates ctx, but app silent-hides chip until
   flipped. Per-sport rollout supported (`<SPORT>:vault_render`).
4. **Audit tool** — `audit_vault_patterns.py --sport MLB [--pattern KEY]`
   spot-checks matches + outcome grading vs raw game_results, prints
   pass/fail per guardrail, samples recent games for manual eyeball.

## Validation flow before flipping shadow off

1. Apply migration `20260901r_vault_render_shadow_flag.sql`
2. Wait 1 cron cycle for compute + attach to populate
3. `python audit_vault_patterns.py --sport MLB` — verify outcomes match reality
4. If clean: `UPDATE feature_flags SET enabled=true WHERE sport='ALL' AND feature='vault_render'`
5. Per-sport rollout supported (validate MLB, ship MLB alone, wait on NFL)

## Cross-references

- [[project-per-source-tracker-moat-818]] — per-source calibration (base infra for external_picks history)
- [[project-dissent-audit-822]] — dissent bucket findings that inspired starter patterns
- [[project-sweat-badges-901]] — parent badge system this fits into
