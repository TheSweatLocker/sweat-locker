---
name: NBA chalk-trap detector + primary play rewrite (2026-05-07)
description: NBA primary play + modelMismatch were firing on net rating gap alone, ignoring market price; rewritten to require model-vs-market edge ≥2 pts before any lean fires
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
NBA primary play and modelMismatch booster now require **explicit model-vs-market edge** before firing leans/boosts. Old logic fired "ML lean" on net rating gap alone (Thunder -15 with NR gap +13 fired LEAN despite zero edge).

**Why:** Andy spotted the issue 2026-05-07 — Thunder -15 favorite with "ML lean" badge that just restated market consensus. Same chalk-blindness pattern as the MLB ML picks we removed in early May. Fixing in two places:

1. **Primary play (calcGameSweatScore NBA branch)** — now computes `model_edge = nrGap + market_spread`. Suppresses primary play when |edge| < 2. When |edge| ≥ 2, fires "ATS edge" or "dog cover" with explicit edge value in sub-label. Tier: STRONG at edge ≥ 4, LEAN at edge ≥ 2.

2. **modelMismatch boost** — old: `min(20, nrGap * 1.5)` boosted modelMismatch on chalk games. New: only boosts when `|nrGap + marketSpread| ≥ 2`, with ramp `min(20, (edge - 1) * 4)`. No edge → no boost → no Sweat Score inflation on chalk.

**How to apply (additional improvements bundled today):**
- **Star OUT suppression** — when `injury_note` includes 'OUT', primary play fires "Star OUT — skip lean" instead of any lean. Covers uncalibrated star impact.
- **Pace-adjusted total edge** — when no spread edge fires, project total via (off+def)/100 × pace and surface OVER/UNDER lean when |projected - market| ≥ 5pts.
- **Recency drift modifier** — L10 net rating - season net rating per team. When `|home_drift - away_drift| ≥ 2`, append "L10 trending up/down" to lean label.
- **Pace-adjusted total** uses formula `home_score = (home_off + away_def)/2/100 * avg_pace`.

**Threshold rationale:**
- 2pt edge = ~70-75% NBA spread cover historical (rough — needs Phase 2 cohort audit)
- 4pt edge = STRONG tier (~80%+ historical when threshold met)
- 5pt total edge ≈ 1.5 runs equivalent for MLB (where audit cohort total_edge_under_1_5_to_3 hits 100% on n=2)

**Phase 2 follow-up (Nov+):** Add NBA spread_delta cohort to mlb_tier_calibration.py the same way we added NBA NR_gap cohorts on 5/6. After 30+ regular-season games, calibrate the 2pt and 4pt thresholds against actual hit rate. Currently just informed-guess thresholds based on MLB analog.

**Phase 2 server-side migration shipped 2026-05-08:**
- Schema: `supabase/migrations/20260508_nba_game_picks.sql` (table `nba_game_picks`)
- Generator: `mlb_pipeline/nba_picks_generator.py` — daily server-side picks with chalk-trap + pace-adj total + star OUT skip + recency drift modifier
- Resolver: `nba_pick_logger.py --resolve` extended with `resolve_picks()` that grades ml/ats/total picks from `nba_game_results` scores
- Audit: `audit_tier_calibration.py` adds `nba_pick_{prime,strong,lean}_{ml,ats,total}` cohorts (9 keys), reads from `nba_game_picks`
- Cron: `.github/workflows/mlb_pipeline.yml` adds "Generate NBA conviction-tier picks" step between log_picks and resolve
- Tier thresholds (post-shakedown 2026-05-08):
  - Spread: LEAN ≥2 / STRONG ≥4 / PRIME ≥6 (unchanged)
  - Total: LEAN ≥7 / STRONG ≥11 / PRIME ≥15 (raised from 5/8/10 — original fired PRIME on 4/4 of a 4-game slate)
- **Playoff pace dampener**: `PLAYOFF_PACE_FACTOR = 0.96` applied to avg_pace when `is_playoff_time()`. BDL returns season-averaged pace; playoff games run ~3-4% slower (halfcourt sets, fewer transitions). Without this, totals systematically projected ~9pts above market during playoffs and every game fired OVER PRIME.
- Star OUT rows use sentinel `pick_side='skip'` (NOT NULL) so the unique constraint dedupes on re-run
- **Shakedown finding 2026-05-08**: 5 picks across 4 playoff games — 2 PRIME (both dog covers, real chalk-trap territory), 3 LEAN. Spread distribution symmetric (2 dogs, 2 favorites). Re-validate threshold + dampener once 30+ resolved games hit the audit cohort

Commits: post-2b7b325 follow-on (5/7); Phase 2 schema + generator + resolver + audit + cron (5/8, pending commit).
