---
name: project-madden-top100-nfl-signal-824
description: "8/24 brainstorm — inject Madden NFL 27 team + player ratings + NFL Top 100 peer-voted list as roster-talent priors into NFL model, especially Weeks 1-3 before EPA sample stabilizes."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-24T02:45:35.301Z
---

**Brainstorm captured 8/24 for tomorrow's work.** User called it for the night — this is the queued spec.

## Core idea
Madden NFL 27 launched 8/13/26 with 2,362 rated players + official 32-team OVR/OFF/DEF ratings. NFL Top 100 (peer-voted) rolls out through early Sept. Combined, they form a structured roster-strength prior that's especially valuable Weeks 1-3 when EPA sample size is thin and the market is over-weighting narrative.

## Why it fits our stack
- Team-level OVR/OFF/DEF gap correlates with EPA differentials but is available DAY 1 of season (EPA needs 3-4 games to stabilize)
- Fills the "Week 1-3 uncalibrated" gap called out in [[project_nfl_ncaaf_week1_readiness_820]] — model self-heals by Week 3-4 with real EPA, but before then talent priors are our best proxy
- EA updates ratings weekly during the season → becomes a rolling talent signal, not just a preseason snapshot
- Top 100 is peer-voted → captures intangibles / scheme value / clutch that pure attribute ratings miss (complementary, not duplicative)
- Similar architecturally to what we just did for [[project_roster_physicality_823]] with NCAAF/NCAAB — just a different data source and cadence

## Reference: launch OVR/OFF/DEF snapshot (8/13/26)
- Top 5: LAR 90, PHI 88, BAL 88, DEN 87, NE 87
- 99 Club: Chase (WR CIN), JSN (WR SEA), Josh Allen (QB BUF), Stafford (QB LAR), Garrett (EDGE LAR), McBride (TE ARI)
- Bottom 5: MIA 74, TEN 76, ARI 76, LVR 77, NYJ 77

## Proposed feature engineering

### Team-level
- `madden_off_gap` = home_off_rating - away_def_rating (spread signal)
- `madden_def_gap` = home_def_rating - away_off_rating (spread signal)
- `madden_ovr_gap` = home_ovr - away_ovr (baseline talent gap)
- Z-scored across 32 teams so scale is model-friendly

### Position-group
- `qb_rating_delta` = home_qb_ovr - away_qb_ovr (biggest single-player edge in football)
- `skill_top3_avg_delta` = top-3 skill position player avg (WR/TE/HB blended)
- `ol_starter_avg_delta` (starting 5)
- `edge_top2_delta` (pass rush)
- `secondary_top3_delta` (CB + S)

### Top 100 tier boosts (once final list drops early Sept)
- Tiered additive: Top 10 = +12%, 11-25 = +8%, 26-50 = +4%, 51-100 = +2% on relevant efficiency metrics
- Or continuous: 1/rank or log-rank (smoother, no cliff)
- Position-weighted (QB and pass-rusher Top 10 hits harder than RB Top 10)

## Signals to seed (shadow mode first)
- `nfl_madden_off_edge_home` — off gap ≥ 8 pts → HOME_ML
- `nfl_madden_def_edge_home` — def gap ≥ 8 pts → HOME_RL
- `nfl_qb_talent_advantage` — QB rating delta ≥ 10 → team ML
- `nfl_top100_elite_qb` — QB ranked Top 10 → team ML small boost
- `nfl_dual_top_defense` — both defenses ranked ≥ 87 OVR → UNDER
- `nfl_talent_mismatch_early` — OVR gap ≥ 10 AND Week ≤ 3 → favored side (larger weight because market underweights talent early)

All shadow (0.30-0.45 strength cap, `enabled=true` but low-weight) until 30-45d of graded games. Follows discipline pattern from [[project_playbook_shadow_tracking_820]] and [[project_roster_physicality_823]].

## Data ingestion path
1. **Static ratings scraper** — one-time launch snapshot from EA official ratings DB. Store in `nfl_madden_ratings` table (team, position, player, ovr + key attrs, week_snapshot, source_url).
2. **Weekly refresh** — EA updates ratings weekly. Cron in nfl_pipeline.yml Tue morning pull. Delta from prior week is its own signal (biggest riser/faller as breakout/regression flag).
3. **Top 100 pull** — once all 100 revealed (early Sept), scrape once + lock for season. Store in `nfl_top100_snapshot` table.

## Risks to think through tomorrow
- **Circularity** — EA adjusts ratings based on performance, so model may learn what would emerge from EPA anyway. Mitigation: weight Madden signal HIGHER Weeks 1-3, DECAY through season as EPA takes over.
- **99 Club quirks** — position scarcity + marketing bias (e.g., Trey McBride 99 seems generous). Don't use "99 = elite" as a threshold; use continuous scale.
- **Roster churn** — EA updates lag Wednesday morning after games. May be stale for Thu Night Football.
- **Top 100 late reveal** — Final list drops early Sept, right around Week 1. May need placeholder using EA rankings for Week 1 before Top 100 lands.

## How to apply
When picking up this thread tomorrow: start with the scraper + schema (~2h), seed 3-5 shadow signals (~1h), wire into `nfl_game_context.py` enricher (~1h). Total ~4h for MVP shadow deployment. Reweight after 30d of graded data alongside the existing [[project_playbook_reweight_821]] refit cycle.
