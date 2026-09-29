---
name: external-aggregation-launch-feature
description: "External pick aggregation tab — per-sport pull cadence, source-priority tiers from 7/20 audit, legal attribution best practices. Differentiator: no MLB app aggregates 15+ handicappers with fade tags."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-22T02:23:06.468Z
---

**Set 2026-07-21 during design session.**

## Feature vision

Per-game external tab surfacing 15+ handicapper picks. Fresh pull + fade tags (from audit) + link-back. Saves users 30-45 min manual research daily.

**No other MLB app does this consolidated view with fade tags.** Differentiator.

## Per-sport pull cadence

| Sport | Primary pull time (ET) | Refresh | Notes |
|---|---|---|---|
| MLB | 12:00 PM | 5:00 PM | Handicappers post 10-noon; Action Network public $ settles |
| NBA | 4:00 PM | 30 min pre-tip | Season Oct-Jun |
| NHL | 3:00 PM | 30 min pre-puck | Season Oct-Jun |
| NCAAB | 5:00 PM weekday / 10:00 AM weekend | 60 min pre-tip | Nov-Mar |
| NCAAF | Sat 8:00 AM | 3 hrs pre-kick | Aug-Jan |
| NFL | Wed 6:00 PM (props) + Sun 8:00 AM (spreads) | 90 min pre-kick | Sep-Feb |
| UFC | Sat 12:00 PM | 4 hrs pre-first-fight | Weekly |

## Source-priority tiers (from 7/20 external audit)

**BOOST (real signal per audit):**
- Dimers ≥60% win prob
- CBS staff picks / Selesnick dissents (75% n=4)
- Public bets ≥70% AND price ≤−150 (chalk-only lens 4-0)
- Action Network sharp $ money gap ≥ +35 (only credible band)

**TRUST (confluence vote, no boost):**
- Doc Sports agreement with pipeline
- VSiN Peterson best bets
- BettingPros expert SGP
- PickDawgz free picks
- Pickswise 3-star (only, not 5-star)
- Covers expert lean
- OddsShark computer picks
- Fangraphs win probability

**NEUTRAL (informational, no vote):**
- Handicapper single-source (any) unless confluence hits 3+
- SCP (small coverage)

**FADE (audit-flagged, treat as inverse or ignore):**
- Ballpark Pal wind direction calls (0-3 on 7/20)
- Pickswise 5-STAR ratings (0-2 tier)
- Action Network sharp $ mid-gap +15 to +34 (1-3 trap zone)

## Storage schema

`external_picks` table (see project_external_aggregation_launch for full DDL). Key columns: game_id, sport, source, surface, pick_side, pick_line, odds, confidence, raw_text (attribution), source_url, pulled_at, fade_flag.

Deduplication key: (game_id, source, surface, pulled_at day).

## Legal / attribution best practices

**Yes, you can name external sources with proper practices.** Compliant with fair use + nominative fair use for identification.

**Required per source card in UI:**
1. Source name prominent (attribution)
2. Direct link back to source (drives them traffic, appeases)
3. Short excerpt only (< 30 words safe harbor)
4. Fresh pull timestamp visible
5. Your fade/boost tag clearly labeled as YOUR analysis

**Skip entirely (paywall/premium):**
- The Athletic
- SportsLine premium
- Any subscription-gated content

**Terms of Service disclaimer to add:**
> "Sweat Locker aggregates publicly available picks and analysis from third-party handicappers for informational purposes. All picks are attributed to their original source with a link back. Sweat Locker does not endorse or recommend third-party sources; users should visit the original site for full analysis."

## Implementation phases

**Phase 1 (pre-launch):** MLB only, noon + 5PM cron, 15 sources, per-game tab in app.

**Phase 2 (post-launch NFL Sep):** Add NFL Wed/Sun cadence.

**Phase 3 (Oct):** Add NBA + NHL cadence.

**Phase 4 (Nov-Jan):** NCAAB/NCAAF/UFC.

## Status 2026-07-21 — LIVE

**43 picks/day writing to `external_picks` across 7 working sources:**
- Action Network 15 (Playwright, bet%)
- Dimers 9 (Playwright, win prob)
- Pickswise 8 (requests, star ratings + auto-fade on 5★)
- PickDawgz 3 (requests article crawl)
- BettingPros 3 (Playwright, player props w/ 5★ + EV%)
- VSiN 3 (Peterson best bets)
- Covers 2 (requests table parse)

Playwright + Chromium install baked into `.github/workflows/mlb_pipeline.yml` install
step w/ `continue-on-error` on the pull step so a chromium failure never blocks the
main pipeline. Shared `mlb_pipeline/_playwright_helper.py` (force-added past _*.py
scratch rule) means NFL/NBA/etc. pull_externals_<sport>.py can reuse the same
render_page() with a different URL slug.

**App-side render shipped:** `app/components/ExternalPicksPanel.tsx` wired
into game detail modal (MLB-only). Fade-flag chips, consensus %, source
attribution, show-more toggle, legal disclaimer footer.

**Deferred to Phase 3+:**
- Money% column (Action Network Pro-locked)
- Anytime-TD prop pull (needs red-zone target share server-side)
- Top-level "Sources" tab (currently only per-game surface; promote v1.1
  based on modal-scroll analytics)

## Related

- [[project_audit_battery_721]] — external lens audit that produced source tiers
- [[project_launch_priorities_july]] — mid-Aug launch context
- [[project_cohort_engine_universal_architecture]] — same universal-cohort pattern
