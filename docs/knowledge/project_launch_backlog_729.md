---
name: launch-backlog-729
description: Pre-launch backlog compiled 7/29. Items surfaced during game-detail v2 first-look testing + user asks. Not blockers for tonight but must be closed before mid-Aug launch.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-22T01:35:43.769Z
---

**Compiled 2026-07-29 during GameDetailV2 iteration sessions.**

## From game detail redesign first-look

1. **Onboarding review** — user asked to add to queue. Current onboarding
   (app/index.tsx step 0-N) may need refresh given all the new signals
   in the game detail (money flow, alignment, v2 cohorts, projections).
   Walk through what a new user sees on first launch and decide what needs
   updating. Especially: explain money%/bets% + alignment badge.

2. **Sport-specific slots for NFL/NBA/UFC/NHL** — currently
   GameDetailV2.tsx shows placeholder "coming next" text for these
   sports. Need to build:
   - NFL/NCAAF: QB vs defense card + injury deltas
   - NBA/NCAAB: Pace/net-rating card + rest days
   - UFC: fighter reads + method/round/distance breakdown from ufc_picks
   - NHL: goalie matchup + back-to-back chip

3. **Odds display formatting** — DONE: `fmtOdds` helper adds `+` prefix
   for positive American odds throughout.

4. **HRB tile toggle behavior** — DONE: tiles now select-on-tap
   (highlighted), then Log Pick + Add Parlay act on the selected tile.

5. **LAD-style empty state** — SEA @ LAD showed missing lens data initially.
   Root cause was frontend LensGrid filtered null values. FIXED — now
   shows all 5 lens with `—` for missing values instead of hiding rows.
   Broader question: which games have consistent lens data vs which
   need pipeline enrichment? Audit needed.

6. **Jerry lifetime % hardcoded** — DONE: removed "60% lifetime" text.
   Longer-term: build a `lens_lifetime_stats` table that populates via
   a nightly cron, then the app can display live % without app rebuilds.

## Added 2026-07-29 EOD

19. **Receipts tab review** — audit the Receipts tab UX end-to-end. Add
    sport-dependent filter/tab (MLB / NFL / NCAAF / etc.) so users can
    slice track record per sport. Right now it's likely aggregated across
    everything which hides sport-specific signal quality.

20. **Daily Degen stats surfacing decision** — DD parlay record 7-84 (8%)
    is ugly to display publicly. Options:
    - Don't surface parlay record at all (internal only)
    - Surface LEG-level record instead (190-148 = 56.2%, respectable)
    - Surface with framing: "Individual pick accuracy 56.2% · parlay
      hit rate 8% (parlays are high-variance by design)"
    - Wait until we're break-even before publishing
    Recommendation: lead w/ leg-level %, tuck parlay record behind a
    "how DD works" explainer that owns the variance framing.

## Added 2026-07-29 EOD pt 2

21. **GameDetailV2 multi-sport risk** — MLB shipped end-to-end tonight. Risk
    at Aug 7 preseason kickoff (NFL): the parent's ctx lookup in
    app/index.tsx only handles MLB + NFL/NCAAF cases; NBA/NCAAB/UFC/NHL
    return null. Externals fetch still works (GameDetailV2's derive-by-team
    fallback kicks in), but sport-specific slots + lens grid + score
    projections need per-sport data pipes:
    - NFL: lens fields exist (projected_spread/projected_total/conf) but
      no per-game props table. QB matchup slot content needed.
    - NCAAF: same as NFL, Aug 22 target
    - NCAAB: needs lens fields wired to game_context; Nov target
    - UFC: needs sport slot showing fighter reads + method/round/distance
      from ufc_picks; ufc_upcoming_event has fights, not games
    - NHL: no pipeline yet; whole sport needs build
    Action for tomorrow: audit which sports have game_context populated
    for their upcoming season and note gaps.

22. **More free external picks** — user asked. Sports Chat Place +
    Odds Shark scrapers are the next two adds (each ~2h). Both stubbed
    in pull_externals_mlb.py already.

23. **Handicapper totals surface** — DONE in GameDetailV2. Random total
    picks from bettingpros/pickswise/pickdawgz now render in dedicated
    Total OVER/UNDER buckets alongside ML.

## From earlier sessions still open

7. **TestFlight rebuild + submit** (deferred per user 7/29)
8. **RevenueCat + App Store Connect setup** (deferred per user 7/29)
9. **Website + ToS/Privacy externals disclosure** — app copy done; web
   copy drafted for paste (see conversation logs)
10. **NBA nba_api migration** — post-launch, season Oct/Nov
11. **NHL full pipeline** — post-launch, season 2026-27
12. **UFC method/round/distance markets** — Odds API tier doesn't
    expose; need DK/FD scrape or tier upgrade
13. **Sports Chat Place + Odds Shark scrapers** — post-launch v1.1, each ~2h
14. **RLM detection** — needs oddscrowd snapshot storage as time-series
15. **Series_letdown v2 cohort bug** — 0 fires, needs same-series
    detection logic fix (currently in cohorts_v2.py)
16. **KenPom chalk parlay finder** — high-EV strategy, mid-November
    NCAAB season start
17. **compare_v1_v2_nightly cron** — script exists, needs GitHub
    Actions wiring (nightly 2am ET)
18. **Prop pipeline pickdawgz fix regression check** — bumped 2→9 games
    7/29 but verify it holds across weekly variance

## Added 2026-08-21

24. **Admin live-note pane** — user asked. Invisible-when-empty message
    box in-app that surfaces a short live note the user populates
    remotely (Supabase row → app reads on load / poll). Use cases:
    "picks locked early tonight, awaiting resolution", "stale lines,
    give us a min", any urgent FYI that shouldn't require an app
    submission cycle. Design: single `admin_notice` table (id, message,
    severity 'info'/'warning'/'critical', starts_at, expires_at,
    dismissible bool). App component renders a top-of-screen banner
    only when a live row exists in the window. User can set/clear via
    a tiny admin UI (or direct Supabase for MVP). Keep separate from
    push notifications — this is passive in-app surfacing.

## Related
- [[project_game_detail_redesign_729]] — the design spec being implemented
- [[project_launch_priorities_july]] — original launch scope
- [[project_pricing_launch_decision]] — pricing locked $14.99/$119.99
