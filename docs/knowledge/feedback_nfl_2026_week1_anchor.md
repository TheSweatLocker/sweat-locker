---
name: nfl-2026-week1-anchor
description: "NFL 2026 Week 1 = Thu 9/11 (TNF) through Mon 9/15 (MNF). Do NOT call the 9/13-9/15 slate \"Week 2.\""
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-14T00:51:52.059Z
---

**Rule (Andy authority):** In Sweat Shop 2026, **NFL Week 1 = 2026-09-09 through 2026-09-15 inclusive**. Sunday 9/13 slate + Monday 9/14 MNF (DEN @ KC) are all Week 1. **Week 2 starts Tuesday 2026-09-16.**

**Anchor for arithmetic:** `2026-09-09` (season_week = floor((game_date - 2026-09-09) / 7) + 1).

**Why:** Andy has corrected me on this repeatedly. Real-world NFL 2026 might use different dates but the environment's convention aligns with what `nfl_game_context.week` stores in the DB (`week=1` for game_date=2026-09-13/14/15).

**How to apply:** Anywhere I compute or reference NFL season week for this environment:
- Week 1 Thu anchor = **2026-09-09**, NOT 2026-09-04
- The season_week helper I shipped in migration `20260913f_nfl_season_week_canonical.sql` uses the wrong anchor (2026-09-04) — needs a follow-up migration to correct.
- Client `_seasonWeekAnchors` in `app/index.tsx` also uses 2026-09-04 — needs the same correction.
- Any read enrichment (KEY PLAYERS aggregator, injury week fetch, etc.) that assumes 9/4 anchor is off-by-one.

**Historical corrections logged (stop repeating):**
- 2026-09-13: called 9/13-9/15 slate "Week 2" multiple times in prose to Andy. WRONG. It's Week 1.
- Same day: shipped `season_week` migration with 9/4 anchor. WRONG. Needs re-backfill to use 9/11.

**Do NOT reason from a mental model of the real 2026 NFL schedule.** The Sweat Shop DB is authoritative. When in doubt, `SELECT DISTINCT week, game_date FROM nfl_game_context WHERE season=2026 ORDER BY game_date` and see what the DB actually stores.

Related: `feedback_verify_player_team_first` (same "trust the DB not my mental model" pattern).
