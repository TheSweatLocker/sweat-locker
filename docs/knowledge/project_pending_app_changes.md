---
name: App changes deployed vs pending in TestFlight
description: Tracks what's in the deployed TestFlight build vs what's only on main. Build cut & went live 2026-05-12.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
**LATEST TESTFLIGHT BUILD: cut & live 2026-05-12 (afternoon).** Included the full batch below.

**What's IN the live build (deployed 2026-05-12):**
- Jerry MLB read hot/cold streak prose (L10 vs season R/G, 🔥/❄️ flags)
- Expected-Ks display swap (no more "Over 5.1" / -700 trap) + L7-rolling fallback for `_projected_ks` (fixes Skenes/Sánchez where season-K%-parse missed)
- BB / Hits-Allowed prop labels ("0.9 expected walks (under 1.5)" etc.)
- Outs / ER prop labels filled in (were raw prop_type strings)
- Add to Parlay button on prop cards
- 📝 Log Pick button (single-bet tracker)
- Same-game correlation ⚠ chip
- 3-counter split (Games / Props / Parlays) in My Picks header
- Jerry parlay analysis anti-hallucination prompt rules + prop-signal grounding
- Pitcher Scouting panel on game detail → Stats tab (MLB) — reads `pitcher_projections` table
- MLB Situational tab fully built (NRFI lens, weather/park, umpire flags, bullpen workload, recency drift, pitcher situational, model edge snapshot) — reads `mlb_umpires` table; no hardcoded audit % (uses zone/rate descriptions)
- `fetchMLBGameContext` extended to pull `pitcher_projections` (200) + `mlb_umpires` (300)
- Book Consensus tab chip de-capsed
- Brand tagline fixes ("MORE DATA. LESS SWEAT.")
- FadesScanner removed (dead code)

**Server-side changes also live (don't ride app builds — already in cron):**
- xERA gap rule audit-corrected: fires OVER lean only in 2.0-3.0 band (validated 58.2%), ≥3.0 band no longer fires (52% coin flip). Cohorts `xera_gap_2_3_over` / `xera_gap_ge3_over` / `xera_gap_ge2_over` now tracked.
- Hits-UNDER PRIME multi-signal gate; barrel% slump detector in hits Over/Under
- Pitcher Walks O/U + Hits-Allowed O/U prop generators (projection-first); resolver grades bb/ha/outs/er (was a gap)
- compute_pitcher_class_projections.py uploads to `pitcher_projections` table; cron reordered (projections before generate_props)
- K-Over alt-line + total/bullpen correlation cohorts; total-factor + spread/ML factor cohorts; pitcher rest cohort; umpire cross-cohorts; pitcher class projections (Phase A)

**QUEUED FOR NEXT TESTFLIGHT BUILD (on main / planned, NOT in the live build):**
1. ~~fetchGameNarrative prompt hardening~~ — SUPERSEDED by the server-side Jerry migration below (the date-confusion guardrail now lives in the `game_read_wrapper` row in `prompt_templates`, fixed there, not in an app build).
2. **Server-side Jerry — full coverage shipped, pending TestFlight build:** `fetchGameNarrative` now checks `jerry_cache` for MLB/NBA/NFL/UFC reads first (per-sport key schemes); falls through to legacy client generation only on miss. "📊 THE NUMBERS" panel renders the pipeline-edge struct, sport-aware (MLB pitcher blocks + props; NBA efficiency + ATS/total picks; UFC method/round/edge struct; NFL market+EPA). `fetchParlayAnalysis` + `fetchPickRecap` fetch templates from `prompt_templates` at call time (with bundled fallback). Server side: `generate_mlb_game_reads.py`, `generate_nba_game_reads.py`, `generate_ufc_game_reads.py`, `generate_nfl_game_reads.py` all wired into mlb_pipeline.yml (self-no-op when sport is offseason). STILL TODO in a later pass: best-prop blurb + the supabase-cached chat + daily-chat prompts also moved to the table.
3. (add anything new that lands in main but misses the next cut)

**Migration dependencies (all applied):** `pitcher_projections` table (5/11), `nba_game_picks` table (5/8). `mlb_umpires` pre-existing.

**How to apply:** Before the next build, re-check this file. After it cuts clean + QA passes, move the queued items into the "IN the live build" section, note the new cutoff date.
