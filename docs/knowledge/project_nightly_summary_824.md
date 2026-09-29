---
name: project-nightly-summary-824
description: 8/24 marathon session summary — 31 commits shipped across pre-launch surface. Supabase upgrade + massive uncommitted-code cleanup + backend hardening.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-25T02:14:10.858Z
---

**8/24 marathon session — 31 commits shipped.**

## Infrastructure
- Supabase project upgraded: Free tier Nano → Pro plan + Micro compute ($40/mo total). CPU/mem was pegged at 94% causing cascading workflow timeouts. Now healthy sub-2s reads.
- Added missing indexes (`20260824d_missing_indexes.sql`) on hot-query columns (updated_at, captured_at, fetched_at) — prevents future compute exhaustion.
- Added Supabase capacity watchdog (`20260824a_supabase_capacity_rpc.sql`) with 70%/90% alerts.

## Uncommitted code cleanup (the big one)
E2E audit surfaced massive drift between local and remote:
- **37 production Python scripts** never committed (commit `56127548`) — GH Actions was silently failing on missing files across all sports.
- **44 SQL migrations** applied to prod DB via Supabase Studio but never saved to git (`a4eb6d2e`).
- **5 workflow YAMLs** with RLS `SUPABASE_SERVICE_ROLE_KEY` fallback edits uncommitted (`cb810533`).
- **8 pipeline scripts** with accumulated feature work uncommitted (+561 lines net, `49e79223`) — includes tier_discipline sharp-fade rules, NCAAB cohort backfill overhaul, grader multi-sport improvements.
- **4 NBA legacy scripts** deleted locally but still tracked — staged deletion.
- **FAQ screen** (`app/faq.tsx`) wired into router but untracked → would 404 on fresh checkout.
- **Fadereport migration** gutted to empty locally → restored from HEAD before it wiped live data.
- Dead workflow ref (`mlb_pipeline.yml:403` → `_fit_prop_refit_weights_v2.py`) neutralized.

## Playbook + Ensemble root-cause fixes (major discovery)
- **FADE math bug (`7f17b63e`)** — `_compute_edge_and_rec` was reading wrong-side book odds on FADE decisions → all 30d FADE edge_pp values in `prop_playbook_decisions` were mathematically wrong. Explained the "FADE hit rate INVERSELY correlates with confidence" pattern (36% at 30+ edge). Not a signal quality problem, a math artifact.
- **resolveTier FADE-block (`ffeeca9f`)** — sweat card no longer lifts a listed prop to PRIME when playbook is fading it (backing the opposite direction).
- **Ladder direction gate (`b09e2e1e`)** — ladder qualifier hard-blocks FADE-side props from qualifying. Springs Under 3.5 ER wrong-side rung deleted + ladder regenerated correctly to Whisenhunt Under 4.5 Ks (PRIME BACK).
- **sweat_tier coherence gate (`9062696a`)** — sweat_tier can no longer exceed primary_play.tier. Kills the "PRIME sweat chip on a LEAN pick" divergence (was hitting 5 games/day).
- **Coherence disclosure (`ba0dfc68`)** — total pick that contradicts our own model by ≥1 run now shows ⚠ warning in sub-line ("V4 projects OVER 9.5 vs mkt 8.0 — outweighed by other signals"). Brand-aligned transparency.

## Prop pipeline hardening
- **Juice-gate column bug (backtest +22u/30d)** — `_cap_under_juice_trap` + `_cap_hits_over_juice_trap` were reading `book_line` (the LINE value like 1.5) instead of `book_over_odds` / `book_under_odds`. Never fired. Fixed → 42 under-juice-trap props/30d now correctly demoted.
- **hits_over null-odds gate (`9bc288d6`)** — 100% of missing prop odds are hits_over (books offer at -300 to -400 juice outside scraper range). Prior: PRIME chip shown with 0 unit sizing (confusing). Now: cap to LEAN if odds null.
- **PRIME threshold raised 0.40 → 0.60 (`18d0e78e`)** — 30d backtest showed 0.60-0.70 dead zone (50% hit / -0.90u). Kills the middle rollercoaster while keeping 0.70+ elite zone (70%) at PRIME.
- **refit_train_v2.py Windows path hotfix (`1bc6055d`)** — hardcoded `C:\Users\gomez\...` .env path broke GH Actions Linux runner.

## App-side UX
- **Onboarding 3 → 7 steps + explicit ToS/Privacy consent (`28f2cce7`, `f84beb1f`)** — full product tour (Sweat Card, Steam Room, Game Detail, Receipts) + versioned consent record.
- **NCAAFSlot component (`36c27313`)** — dedicated NCAAF sport slot replacing NFLSlot reuse (which queried empty nfl_team_stats). Surfaces SP+, physicality, returning production, MODEL READ card.
- **Public Splits panel in GameDetailV2 (`1e56ec53`)** — renders splits_summary with source badges + TRIPLE CONFIRMED markers.
- **POTD narrative bug (`8e0e985f`)** — self-hydrates pitcher/xERA/venue from mlb_game_context when POTD context is thin + refusal guard prevents Claude "I need more data" text from shipping to users. App fallback for empty narrative.
- **Per-sport admin banner (`4c5ce525`)** — banner now respects `currentSport` prop. `admin_notice.sport='NCAAB'` shows only on NCAAB tab. App-wide notices unchanged.
- **"The X" personality tags for external handicappers (`41d4097e`)** — replaces AN/DM/CV/PW/PD abbreviations with The Book / The Grinder / The Volume / The Chalk / The Dog / The Fade / The Lock / The Consensus / The Line / The Nerd / The Park / The Spread / The Pulse. Brand-cohesive with The Sharp, The Ledger, The Locker. Zero source-name exposure.

## New features
- **Roster physicality Phase 2 compound signals (`ba7e...`)** — 8 signals combining OL/DL weight with EPA/KenPom for NCAAF/NCAAB (Weeks 1-3 heavy).
- **Madden NFL 27 talent priors MVP** — full stack shipped (3 tables + 12 ctx columns + 9 shadow signals + seed script + enricher + workflow wire-up). Fills the Weeks 1-3 EPA-sample-thin gap.
- **NCAAF pre-season signals migration (`20260824e`, 10 signals)** — SP+ tiers, returning production, HFA baseline, moderate physicality gap. Fixes "only 2 signals firing on TCU@UNC" bare card.
- **Sport-state auto-flip cron (`18d840fd`)** — reads `season_start` / `season_end`, flips state automatically. Clears state_message on in_season transition. NCAAF flips 8/29, NFL 9/4, NHL 10/8, NBA 10/22, NCAAB 11/3.
- **Steam Room fix workflow (`de99d7b4`)** — manual GH Actions dispatch for ladder + Line Movement diagnostic.

## Ladder state (as of tonight)
- `ladder_state.active_rung_id = 10` (Whisenhunt U4.5 Ks, PRIME, -117, playbook BACK confirmed)
- `current_streak = 1`, last result W

## Sport readiness (from 30-day scale check)
- MLB: ✅ live, +29u/4d on Sharp Card
- NCAAF: ✅ Week 1 (8/29, 5 days) — roster physicality 99.3% coverage + pre-season signals live
- NFL: ✅ Week 1 (9/4, 11 days) — Madden priors live
- NHL: ✅ 10/8 opener (45 days) — scaffolding + Elo trained
- NBA: ✅ 10/22 opener (59 days) — Elo trained tonight
- NCAAB: ✅ 11/3 opener (71 days) — 4-lens active

## Blockers before TestFlight (tomorrow)
1. Verify migrations applied: 20260824a, e, d
2. Smoke test app: onboarding, POTD, Steam Room 4 tabs, Prop Jerry, Game Detail
3. Cut TestFlight build (main is 31 commits ahead)
4. Address any E2E data audit findings when the agent completes

## Related memory
- [[project_playbook_fade_broken_824]] — FADE root cause (edge_pp math bug)
- [[project_the_x_naming_convention_824]] — personality tag design
- [[project_madden_top100_nfl_signal_824]] — Madden MVP spec
- [[project_roster_physicality_823]] — NCAAF/NCAAB physicality signals
- [[feedback_methodical_no_rerun_spam]] — updated 8/24 with read-audit IO warning
- [[feedback_sweat_card_vs_sharp_card]] — brand distinction saved
