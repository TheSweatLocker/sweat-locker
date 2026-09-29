---
name: project-ncaaf-fcs-coverage-902
description: 9/2 approved — pull FCS SP+ ratings from CFBD + build lightweight FBS-vs-FCS chalk model. Both add real CFB value without huge scope.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-02T17:23:32.290Z
---

🏈 **9/2 approved by user** — extend NCAAF coverage to FCS opponents.
Currently NCAAFFCSNotice component detects FCS matchups + shows a
"our efficiency model doesn't cover FCS" banner. Post-launch:

**Why:** Week 1-3 of CFB is heavy with FBS-vs-FCS games. Right now
those cards feel thin (no v3/v4 output, mostly market-only reads).
Adds real depth to a sport that's live NOW.

**How to apply:** Two workstreams that stack:

## Workstream 1 — Pull FCS SP+ ratings from CFBD

- CFBD endpoint: `/ratings/sp` supports FCS with `classification=fcs`
- Coverage is thinner than FBS but exists for most FCS programs
- New table `ncaaf_fcs_team_stats` mirrors `ncaaf_team_stats` shape
- On FBS-vs-FCS matchup: pull FBS from primary table + FCS from new
  table → same feature vector → same v3/v4 model runs
- Model still trained on FBS-only games so predictions on FCS side
  are extrapolation; acceptable given weak FCS priors

**Ship path:** New puller `ncaaf_fcs_sp_pull.py`, mirror of existing.
Add step to weekly NCAAF cron. Wire into `ncaaf_game_context.py`
feature build to fetch from either table based on classification.

## Workstream 2 — FBS-vs-FCS chalk model

Even simpler and higher-hit for the narrow FCS slice:
- Historical FBS-vs-FCS record: FBS wins ~96% of time
- Average margin: 30+ points
- Standard deviation on margin: moderate — some upsets exist (Appalachian
  State beat Michigan 2007, etc.) but they're outliers
- Model: `expected_margin = fbs_sp_overall - (fcs_sp_overall_or_default)`
  with sensible defaults (bottom-quintile FCS ≈ -25 to -30 SP+)
- Confidence: HIGH on chalk, LOW on totals (FCS defenses vary wildly)

**Ship path:** New scorer `ncaaf_fcs_scorer.py` that runs when v3/v4
return null. Writes to same `projected_spread` / `model_pred_spread`
fields with `_engine='fcs_chalk'` audit tag. LensGrid picks up
automatically since it reads those fields.

## Cross-benefits

- Vault Match can add FCS-specific patterns (e.g. "FBS home fav >21 vs
  FCS covers 60%+" if backtest validates)
- Situational Records cross-sport uniformity — every game has data
- Reviewer safety: no thin cards for reviewers hitting Week 1 FBS-vs-FCS
- ATS-streak pattern recognition (project_ats_streak_patterns_902) can
  fire on FCS matchups too

## Timeline

- Ship Workstream 2 first (few hours) — cheap chalk model that fills
  gaps immediately
- Ship Workstream 1 during Week 2-3 (needs data pull validation +
  backfill of historical FCS SP+)
- Combined coverage lands mid-September, before conference play thickens

## Cross-references

- [[project-ncaaf-model-discussion-queue-902]] — parent CFB model queue
- [[project-ncaaf-ready-809]] — current NCAAF stack (SP+/returning prod/etc)
- [[project-ats-streak-patterns-902]] — pattern recognition workstream
