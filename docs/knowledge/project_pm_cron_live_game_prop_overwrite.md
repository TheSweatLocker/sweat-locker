---
name: project-pm-cron-live-game-prop-overwrite
description: 2pm cron overwrites morning-attached pitcher props with stale internal lines when games have already started; needs preserve-on-live-game guard
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

5/31 incident — 2pm/3pm afternoon cron blanked the morning book line and reverted to internal-line scoring on Misiorowski K Over (and other early-game pitcher props) because the games had already started by cron time. Books pull pre-game prop markets at first pitch; Odds API returns nothing pre-game for live games; Phase 2 attach gets zero results; the scorer overwrites the morning's good record with a stale internal line and inflates conviction.

**Symptom:**
- AM (pre-game): Misiorowski K Over 8.5 ✓book STRONG 75, edge +1.1 vs proj 9.6
- PM (game live): Misiorowski K Over 7.5 ⚠no_book PRIME 93, internal line moved, conviction inflated by missing book attach

This is the same trust-killer pattern as the Brandon Young U 5.5 K incident ([[project_phase2_recal_audit_pending]]) — internal line publishes as PRIME without the book reality check.

**Why it matters:**
- Users tailing the morning card see a different live state than what they bet on
- The PM card / app surface looks "louder" (PRIME 93) than the AM (STRONG 75) on the same player — confusing and erodes trust
- Health check correctly fired the failure (`attach rate 25%`) but only after the damage

**How to apply (queued fix):**
1. **Game-time gate in Phase 2 attach**: if `commence_time <= now()`, skip overwrite. Keep the morning row intact.
2. **OR more nuanced**: attempt Odds API fetch; if it returns empty for an in-progress game, fall back to "preserve morning state" rather than blanking and reverting to internal.
3. **Tier preservation flag**: when book_line is null because the game is live (not because Phase 2 broke), mark the row `book_line_status='locked_pregame'` so the conviction doesn't pretend an edge exists at the internal line.
4. **Surface-side guard**: app should not promote any PRIME prop where `book_line IS NULL` for a game in `live/final` status — fall back to last known book line.

**Related:** [[project_phase2_recal_audit_pending]], [[project_may30_potd_bugs_and_gap]], [[feedback_source_gate_pattern]]

**Separate but adjacent (NOT this issue):** BB props (Miller, Bradish) dropped from the publish set entirely on the PM run despite their games being pre-game. That's a scorer-side regression to investigate independently — could be a tier gate change or a fetcher returning empty BB data, not a live-line issue.
