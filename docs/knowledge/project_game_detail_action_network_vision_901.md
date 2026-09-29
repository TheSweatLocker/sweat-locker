---
name: game-detail-action-network-vision-901
description: "9/1 vision drop — Game detail redesign inspired by Action Network layout, cross-sport. Key delta from their pattern: show RAW stat + rank together, not rank alone."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-01T14:45:17.898Z
---

Vision drop 2026-09-01: user sent screenshots of a competitor app (looks like Action Network) as the reference direction for our Game Detail redesign. Doesn't have to be pixel-for-pixel; treat as the visual language target.

**Sections to build (per game screen):**
- Horizontal tab bar: Picks / PRO / Props / Trends / Stats / Weather / Lineups
- **Team Stats**: Offense Rank / Defense Rank dropdown selectors, mirrored two-column layout (Team A left · stat name center · Team B right) with colored rank pills — green=elite, light-green=above avg, gray=mid, pink=below avg, red=bottom
- **Situational Results**: Spread / Total / Moneyline segmented control, mirrored W-L records for Overall, L10, Home/Away, Fav/Dog (Over/Under variants for Total)
- **Recent Schedule**: 3-tab (Team A / Head-to-Head / Team B) with GAME · RESULT · ATS · O/U columns and colored W/L badges per column
- **Stat Leaders**: player headshot + name + number, mirrored
- **Weather**: field-shape visualization (baseball diamond / football field) with wind arrow at directional position + temp

**🎯 User's key delta from Action Network's pattern (IMPORTANT):**
Instead of showing rank chip alone ("Passing Yards · 2nd"), show **raw stat + rank together** ("Passing Yards · 258 yds/g · 2nd"). Rank without the raw number is uninformative — casual bettors see "2nd" and can't tell if it's 320 vs 200 league median or 258 vs 255. The raw per-game average IS the signal; rank is context.

**Why:** Casual bettor UX docket ([[project_casual_bettor_ux_docket]]) — translation not simplification. Numbers > abstractions.

**How to apply:**
- When redesigning [GameDetailV2.tsx](app/components/GameDetailV2.tsx), replace `RankedStatRow` rank-only chips with `RankedStatRow` that renders BOTH raw value + rank
- Pre-work: audit whether we're currently storing raw per-game averages in `team_stats_summary` JSONB, or only ranks. If only ranks, pipeline change required (per-sport) before UI change lands.
- Cross-sport: apply pattern to MLB / NFL / NCAAF / NCAAB uniformly ([[feedback_universal_vs_sport_specific]])
- Backside dictates render ([[feedback_backside_dictates_app_renders]]) — do the pipeline stat capture first, then the app is just a dumb renderer

**🎯 User directive 2026-09-01 — "well-oiled machine, be methodical" (CORRECTED same-day):**
Before **shipping** any UI redesign work to production, verify EVERY data feed the vision depends on is:
1. **Accurate** — spot-check a sample of games per sport; raw values match source; no rank/raw mismatch
2. **Complete** — every field the layout renders has data for all games (Recent Schedule, Situational Results, Stat Leaders, Weather, Team Stats — no `--` chips in production because the feed is thin)
3. **On a cadence** — pull is scheduled on a cron (not a one-time backfill); ledger written to a table + row age visible; nightly data-quality checker (`audit_data_quality.py`) has a check for staleness
4. **Documented** — per-sport data source matrix in [[reference_data_source_strategy]] updated with cadence + coverage before UI ships

Plot the UI in PARALLEL with the pipeline work — see [[feedback_ui_and_pipeline_in_parallel]]. Don't serialize design behind pipeline. The gate is at prod-ship, not at design-start. Related discipline: [[feedback_methodical_no_rerun_spam]], [[project_signal_framework_821]].

**Related work:**
- [[project_game_detail_redesign_729]] — earlier redesign spec (v2 approved 7/29)
- [[feedback_sample_size_with_pct]] — same principle: numbers over abstractions, always show n
